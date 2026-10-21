"""Offline tests: fake codex/claude executables stand in for the real reviewers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / 'skills' / 'xreview' / 'scripts' / 'xreview.py'

# One fake handles both CLIs. FAKE_MODE picks the behaviour.
FAKE = textwrap.dedent('''\
    #!{python}
    import json, os, sys, time
    args = sys.argv[1:]
    name = os.path.basename(sys.argv[0])
    log = os.environ.get('FAKE_LOG')
    if args[:1] == ['--version']:
        print(name + ' 9.9.9'); sys.exit(0)
    if args[:2] == ['login', 'status']:
        print('Logged in using ChatGPT'); sys.exit(0)
    if args[:2] == ['auth', 'status']:
        print(json.dumps({{'loggedIn': True}})); sys.exit(0)
    if args[:1] == ['sandbox']:
        if os.environ.get('FAKE_SANDBOX') == 'broken':
            print('error building bubblewrap command: unsupported host mount at /data'); sys.exit(1)
        print('read-ok'); print('write-blocked'); sys.exit(0)
    if args[:2] == ['features', 'list']:
        print('shell_tool  stable  true'); sys.exit(0)
    prompt = sys.stdin.read()
    if log:
        with open(log, 'a') as f:
            f.write(json.dumps({{'args': args, 'role': os.environ.get('XREVIEW_ROLE'),
                                 'pid': os.getpid(), 'prompt_len': len(prompt)}}) + '\\n')
    mode = os.environ.get('FAKE_MODE', 'approve')
    if mode.startswith('sleep'):
        time.sleep(float(mode[5:] or 30))
        mode = 'approve'
    if mode == 'exit1':
        sys.stderr.write('boom: model unavailable\\n'); sys.exit(1)
    finding = {{'severity': 'blocker', 'title': 'Divides by n-1', 'file': 'stats.py', 'line': 2,
               'details': 'mean() is biased', 'suggested_fix': 'divide by len(xs)'}}
    review = {{
        'approve': {{'verdict': 'approve', 'summary': 'Looks right.', 'findings': [], 'not_verified': []}},
        'blocker': {{'verdict': 'changes_requested', 'summary': 'One bug.', 'findings': [finding],
                    'not_verified': ['did not run tests']}},
        'inconsistent': {{'verdict': 'approve', 'summary': 'Fine?', 'findings': [finding], 'not_verified': []}},
        'malformed': {{'verdict': 'approve', 'summary': '', 'findings': ['blocker: auth bypass'],
                      'not_verified': []}},
        'extra': {{'verdict': 'approve', 'summary': '', 'findings': [], 'not_verified': [],
                  'blockers': ['auth bypass']}},
    }}.get(mode)
    text = 'this is not json' if mode == 'badjson' else json.dumps(review)
    if name == 'codex':
        out = args[args.index('--output-last-message') + 1]
        with open(out, 'w') as f:
            f.write(text)
        print('codex progress output')
    else:
        envelope = {{'type': 'result', 'is_error': False, 'result': text}}
        if mode != 'badjson':
            envelope['structured_output'] = review
        print(json.dumps(envelope))
''')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='xreview-test-'))
        self.home = self.tmp / 'home'
        self.home.mkdir()
        fakes = self.tmp / 'fakes'
        fakes.mkdir()
        for name in ('codex', 'claude'):
            path = fakes / name
            path.write_text(FAKE.format(python=sys.executable))
            path.chmod(0o755)
        self.config = self.tmp / 'config.json'
        self.write_config()
        self.log = self.tmp / 'fake.log'
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(('CLAUDE', 'CODEX', 'XREVIEW'))}
        self.env.update(HOME=str(self.home), XREVIEW_HOME=str(self.tmp / 'state'),
                        XREVIEW_CONFIG=str(self.config), FAKE_LOG=str(self.log),
                        PATH=str(fakes) + os.pathsep + os.environ['PATH'])
        self.repo = self.make_repo()

    def write_config(self, **extra):
        cfg = {'codex': {'bin': str(self.tmp / 'fakes/codex')},
               'claude': {'bin': str(self.tmp / 'fakes/claude')}}
        cfg.update(extra)
        self.config.write_text(json.dumps(cfg))

    def make_repo(self):
        repo = self.tmp / 'repo'
        repo.mkdir()
        self.git(repo, 'init', '-q', '-b', 'main')
        (repo / 'stats.py').write_text('def mean(xs):\n    return sum(xs) / len(xs)\n')
        self.git(repo, 'add', '.')
        self.git(repo, 'commit', '-qm', 'init')
        return repo

    def git(self, repo, *args):
        subprocess.run(['git', '-C', str(repo), '-c', 'user.email=t@t', '-c', 'user.name=t'] + list(args),
                       check=True, stdout=subprocess.DEVNULL)

    def xr(self, *args, env=None, cwd=None):
        run_env = dict(self.env, **(env or {}))
        proc = subprocess.run([sys.executable, str(SCRIPT)] + list(args), cwd=str(cwd or self.repo),
                              env=run_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, timeout=120)
        return proc.returncode, proc.stdout

    def change(self):
        (self.repo / 'stats.py').write_text('def mean(xs):\n    return sum(xs) / (len(xs) - 1)\n')

    def job_id(self, output):
        for line in output.splitlines():
            if line.startswith('Started '):
                return line.split(' review ')[1].split(' ')[0]
        self.fail('no job id in output:\n' + output)

    def meta(self, job_id):
        return json.loads((self.tmp / 'state/jobs' / job_id / 'meta.json').read_text())

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]


class ReviewFlow(Base):
    def test_codex_approve_collects_diff_itself_and_hides_secrets(self):
        self.change()
        (self.repo / 'new_module.py').write_text('VALUE = 42\n')
        (self.repo / '.env').write_text('API_KEY=fake-secret\n')
        (self.repo / 'blob.bin').write_bytes(b'\x00\x01\x02' * 10)
        code, out = self.xr('start', '--reviewer', 'codex', '--intent', 'Fix mean')
        self.assertEqual(code, 0, out)
        job = self.job_id(out)
        self.assertIn('Not sent (look like credentials): .env', out)
        code, out = self.xr('wait', job, '--timeout', '60')
        self.assertEqual(code, 0, out)
        self.assertIn('Verdict: **approve**', out)
        self.assertIn('XREVIEW {}: approve (0 blocker'.format(job), out)      # full review: no diff-only tag
        prompt = (self.tmp / 'state/jobs' / job / 'prompt.md').read_text()
        self.assertIn('-    return sum(xs) / len(xs)', prompt)
        self.assertIn('+    return sum(xs) / (len(xs) - 1)', prompt)
        self.assertIn('+VALUE = 42', prompt)
        self.assertNotIn('fake-secret', prompt)
        self.assertIn('blob.bin (new binary file)', prompt)
        self.assertIn('Fix mean', prompt)
        call = self.calls()[0]
        self.assertEqual(call['role'], 'reviewer')
        self.assertIn('read-only', call['args'])
        self.assertEqual(call['args'][-1], '-')
        # No user MCP servers, plugins, connected apps or web search for the reviewer.
        self.assertIn('--ignore-user-config', call['args'])
        for feature in ('apps', 'plugins'):
            self.assertEqual(call['args'][call['args'].index(feature) - 1], '--disable')
        self.assertIn('web_search="disabled"', call['args'])
        self.assertNotIn('shell_tool', call['args'])          # full reviews keep the sandboxed shell

    def test_claude_reviewer_is_read_only_and_reports_changes(self):
        self.change()
        code, out = self.xr('run', '--reviewer', 'claude', env={'FAKE_MODE': 'blocker'})
        self.assertEqual(code, 2, out)
        self.assertIn('[BLOCKER] Divides by n-1 (`stats.py:2`)', out)
        self.assertIn('did not run tests', out)
        self.assertIn('changes_requested (1 blocker, 0 major', out)
        args = self.calls()[0]['args']
        self.assertEqual(args[args.index('--tools') + 1], 'Read,Grep,Glob')
        for flag in ('--restricted', '--strict-mcp-config', '--no-session-persistence'):
            self.assertIn(flag, args)
        self.assertNotIn('bypassPermissions', args)
        self.assertNotIn('--dangerously-skip-permissions', args)

    def test_approve_with_blocker_is_downgraded(self):
        self.change()
        code, out = self.xr('run', '--reviewer', 'codex', env={'FAKE_MODE': 'inconsistent'})
        self.assertEqual(code, 2, out)
        self.assertIn('changed the verdict from approve', out)

    def test_unparseable_answer_fails_closed(self):
        self.change()
        for reviewer in ('codex', 'claude'):
            code, out = self.xr('run', '--reviewer', reviewer, '--force', env={'FAKE_MODE': 'badjson'})
            self.assertEqual(code, 1, out)
            self.assertIn('FAILED', out)
            self.assertIn('not an approval', out)

    def test_malformed_findings_are_rejected_not_dropped(self):
        self.change()
        for reviewer in ('codex', 'claude'):
            code, out = self.xr('run', '--reviewer', reviewer, '--force', env={'FAKE_MODE': 'malformed'})
            self.assertEqual(code, 1, out)
            self.assertIn('finding 1 is not an object', out)
            code, out = self.xr('run', '--reviewer', reviewer, '--force', env={'FAKE_MODE': 'extra'})
            self.assertEqual(code, 1, out)
            self.assertIn('unexpected field "blockers"', out)

    def test_review_of_older_changes_is_not_reused(self):
        self.change()
        code, out = self.xr('start', '--reviewer', 'codex', env={'FAKE_MODE': 'sleep3'})
        first = self.job_id(out)
        (self.repo / 'stats.py').write_text('def mean(xs):\n    return 0\n')
        code, out = self.xr('run', '--reviewer', 'codex')
        self.assertEqual(code, 0, out)
        second = self.job_id(out)
        self.assertNotEqual(first, second)
        prompt = (Path(self.meta(second)['job_dir']) / 'prompt.md').read_text()
        self.assertIn('return 0', prompt)
        self.xr('wait', first, '--timeout', '60')

    def test_repo_without_commits_uses_working_tree(self):
        fresh = self.tmp / 'fresh'
        fresh.mkdir()
        self.git(fresh, 'init', '-q')
        (fresh / 'a.py').write_text('OLD = 1\n')
        self.git(fresh, 'add', 'a.py')
        (fresh / 'a.py').write_text('NEW = 2\n')
        (fresh / 'b.py').write_text('UNTRACKED = 3\n')
        code, out = self.xr('run', '--reviewer', 'codex', cwd=fresh)
        self.assertEqual(code, 0, out)
        prompt = (Path(self.meta(self.job_id(out))['job_dir']) / 'prompt.md').read_text()
        self.assertIn('+NEW = 2', prompt)
        self.assertNotIn('OLD = 1', prompt)
        self.assertIn('+UNTRACKED = 3', prompt)

    def test_cancel_right_after_start(self):
        self.change()
        code, out = self.xr('start', '--reviewer', 'codex', env={'FAKE_MODE': 'sleep30'})
        job = self.job_id(out)
        code, out = self.xr('cancel', job)
        self.assertEqual(code, 0, out)
        for _ in range(50):
            if self.meta(job)['status'] != 'running':
                break
            time.sleep(0.2)
        self.assertEqual(self.meta(job)['status'], 'failed')
        time.sleep(1)
        for call in self.calls():  # a reviewer that did start must be gone
            with self.assertRaises(ProcessLookupError):
                os.kill(call['pid'], 0)

    def test_default_reviewer_models(self):
        self.change()
        self.xr('run', '--reviewer', 'codex')
        self.xr('run', '--reviewer', 'claude')
        codex, claude = (call['args'] for call in self.calls())
        self.assertEqual(codex[codex.index('--model') + 1], 'gpt-6-astra')
        self.assertIn('model_reasoning_effort="medium"', codex)
        self.assertEqual(claude[claude.index('--model') + 1], 'claude-opus-5-5')
        self.assertEqual(claude[claude.index('--effort') + 1], 'high')

    def test_null_in_config_uses_the_cli_default(self):
        self.write_config(codex={'bin': str(self.tmp / 'fakes/codex'), 'model': None, 'effort': None},
                          claude={'bin': str(self.tmp / 'fakes/claude'), 'model': None, 'effort': None})
        self.change()
        self.xr('run', '--reviewer', 'codex')
        self.xr('run', '--reviewer', 'claude')
        codex, claude = (call['args'] for call in self.calls())
        self.assertNotIn('--model', codex)
        self.assertFalse(any(a.startswith('model_reasoning_effort') for a in codex))
        self.assertNotIn('--model', claude)
        self.assertNotIn('--effort', claude)

    def diff_only_config(self):
        self.write_config(codex={'bin': str(self.tmp / 'fakes/codex'), 'tools': False},
                          claude={'bin': str(self.tmp / 'fakes/claude')})

    def test_diff_only_codex_gets_no_tools_and_whole_functions(self):
        body = ['def long_function(xs):', '    first_line_marker = 1']
        body += ['    step_%d = %d' % (i, i) for i in range(30)] + ['    return sum(xs)', '']
        (self.repo / 'long.py').write_text('\n'.join(body))
        self.git(self.repo, 'add', 'long.py')
        self.git(self.repo, 'commit', '-qm', 'add long function')
        (self.repo / 'long.py').write_text('\n'.join(body).replace('return sum(xs)', 'return sum(xs) + 1'))
        code, out = self.xr('run', '--reviewer', 'codex')
        self.assertEqual(code, 0, out)
        prompt = (Path(self.meta(self.job_id(out))['job_dir']) / 'prompt.md').read_text()
        self.assertNotIn('first_line_marker', prompt)          # normal reviews: -U5 context
        self.diff_only_config()
        code, out = self.xr('run', '--reviewer', 'codex')
        self.assertEqual(code, 0, out)
        self.assertIn('Diff-only review', out)
        self.assertIn('Mode: **diff only**', out)
        self.assertIn(': approve (diff only) (0 blocker', out)
        job = self.meta(self.job_id(out))
        self.assertTrue(job['diff_only'])
        prompt = (Path(job['job_dir']) / 'prompt.md').read_text()
        self.assertIn('first_line_marker', prompt)             # whole changed function
        self.assertIn('no file or command tools', prompt)
        args = self.calls()[-1]['args']
        for flag in ('shell_tool', 'unified_exec', 'code_mode_host', 'plugins', 'web_search="disabled"'):
            self.assertIn(flag, args)
        self.assertIn('read-only', args)

    def test_repo_mode_gives_codex_repo_tools_instead_of_a_shell(self):
        self.write_config(codex={'bin': str(self.tmp / 'fakes/codex'), 'tools': 'repo'})
        self.change()
        code, out = self.xr('run', '--reviewer', 'codex')
        self.assertEqual(code, 0, out)
        self.assertIn('Repo-tools review', out)
        self.assertIn('Mode: repo tools', out)
        self.assertNotIn('diff only', out)
        job = self.meta(self.job_id(out))
        self.assertEqual(job['codex_mode'], 'repo')
        args = self.calls()[-1]['args']
        self.assertEqual(args[args.index('shell_tool') - 1], '--disable')
        self.assertNotIn('code_mode_host', args)           # Codex calls MCP tools through code mode
        server = [a for a in args if a.startswith('mcp_servers.xreview_repo.args=')]
        self.assertEqual(len(server), 1)
        server_args = json.loads(server[0].split('=', 1)[1])
        self.assertEqual(server_args[1:], ['_repo_tools', str(self.repo.resolve())])
        self.assertTrue(server_args[0].endswith('xreview.py'))
        self.assertIn('--ignore-user-config', args)
        prompt = (Path(job['job_dir']) / 'prompt.md').read_text()
        self.assertIn('xreview_repo tools', prompt)

    def test_switching_mode_is_a_new_review(self):
        self.change()
        self.xr('run', '--reviewer', 'codex')
        self.write_config(codex={'bin': str(self.tmp / 'fakes/codex'), 'tools': 'repo'})
        code, out = self.xr('run', '--reviewer', 'codex')
        self.assertNotIn('already reviewed', out)
        self.assertEqual(len(self.calls()), 2)

    def test_diff_only_applies_to_codex_only(self):
        self.diff_only_config()
        self.change()
        code, out = self.xr('run', '--reviewer', 'claude')
        self.assertEqual(code, 0, out)
        self.assertNotIn('diff only', out)
        self.assertNotIn('shell_tool', self.calls()[-1]['args'])

    def test_reviewer_crash_reports_its_error(self):
        self.change()
        code, out = self.xr('run', '--reviewer', 'codex', env={'FAKE_MODE': 'exit1'})
        self.assertEqual(code, 1, out)
        self.assertIn('boom: model unavailable', out)

    def test_reviewer_timeout_kills_it(self):
        self.write_config(timeout_minutes=0.03)
        self.change()
        code, out = self.xr('run', '--reviewer', 'codex', env={'FAKE_MODE': 'sleep30'})
        self.assertEqual(code, 1, out)
        self.assertIn('did not finish within', out)
        pid = self.calls()[0]['pid']
        time.sleep(0.5)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_wait_returns_while_running_and_review_survives(self):
        self.change()
        code, out = self.xr('start', '--reviewer', 'codex', env={'FAKE_MODE': 'sleep3'})
        job = self.job_id(out)
        code, out = self.xr('wait', job, '--timeout', '0.5')
        self.assertEqual(code, 4, out)
        self.assertIn('still running', out)
        code, out = self.xr('wait', job, '--timeout', '60')
        self.assertEqual(code, 0, out)

    def test_same_diff_is_not_reviewed_twice(self):
        self.change()
        code, out = self.xr('run', '--reviewer', 'codex')
        self.assertEqual(code, 0, out)
        code, out = self.xr('start', '--reviewer', 'codex')
        self.assertEqual(code, 0, out)
        self.assertIn('already reviewed', out)
        self.assertEqual(len(self.calls()), 1)
        code, out = self.xr('run', '--reviewer', 'codex', '--force')
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.calls()), 2)

    def test_changes_to_large_untracked_files_count_as_new(self):
        self.change()
        big = self.repo / 'data.txt'
        big.write_text('a' * 300000)
        code, out = self.xr('run', '--reviewer', 'codex')
        self.assertEqual(code, 0, out)
        self.assertIn('too large to inline', (Path(self.meta(self.job_id(out))['job_dir']) / 'prompt.md').read_text())
        big.write_text('b' * 300001)
        code, out = self.xr('run', '--reviewer', 'codex')
        self.assertNotIn('already reviewed', out)
        self.assertEqual(len(self.calls()), 2)

    def test_different_model_is_a_new_review(self):
        self.change()
        self.xr('run', '--reviewer', 'codex')
        code, out = self.xr('run', '--reviewer', 'codex', '--model', 'other-model')
        self.assertNotIn('already reviewed', out)
        self.assertEqual(len(self.calls()), 2)
        self.assertIn('other-model', self.calls()[1]['args'])

    def test_running_review_is_reused(self):
        self.change()
        code, out = self.xr('start', '--reviewer', 'codex', env={'FAKE_MODE': 'sleep3'})
        first = self.job_id(out)
        code, out = self.xr('start', '--reviewer', 'codex')
        self.assertIn('exactly these changes is already running: ' + first, out)
        self.xr('wait', first, '--timeout', '60')

    def test_cancel(self):
        self.change()
        code, out = self.xr('start', '--reviewer', 'codex', env={'FAKE_MODE': 'sleep30'})
        job = self.job_id(out)
        time.sleep(1)
        code, out = self.xr('cancel', job)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.meta(job)['status'], 'failed')
        self.assertEqual(self.meta(job)['reason'], 'cancelled')

    def test_base_reviews_committed_work(self):
        self.git(self.repo, 'checkout', '-q', '-b', 'feature')
        self.change()
        self.git(self.repo, 'commit', '-qam', 'change mean')
        code, out = self.xr('start', '--reviewer', 'codex')
        self.assertEqual(code, 1)
        self.assertIn('nothing to review', out)
        code, out = self.xr('run', '--reviewer', 'codex', '--base', 'main')
        self.assertEqual(code, 0, out)
        prompt = (Path(self.meta(self.job_id(out))['job_dir']) / 'prompt.md').read_text()
        self.assertIn('since main', prompt)
        self.assertIn('(len(xs) - 1)', prompt)

    def test_large_diff_is_truncated_by_file(self):
        self.write_config(max_diff_kb=2)
        self.change()
        (self.repo / 'big.py').write_text('x = 1\n' * 2000)
        code, out = self.xr('run', '--reviewer', 'codex')
        self.assertEqual(code, 0, out)
        prompt = (Path(self.meta(self.job_id(out))['job_dir']) / 'prompt.md').read_text()
        self.assertIn('(len(xs) - 1)', prompt)
        self.assertIn('big.py (diff omitted, over the size budget', prompt)
        self.assertNotIn('+x = 1', prompt)
        self.assertLess(len(prompt), 12000)


class Guards(Base):
    def test_agent_cannot_review_itself(self):
        self.change()
        code, out = self.xr('start', '--reviewer', 'claude', env={'CLAUDECODE': '1'})
        self.assertEqual(code, 1)
        self.assertIn('cannot review its own work', out)
        code, out = self.xr('start', '--reviewer', 'codex', env={'CODEX_THREAD_ID': 'abc'})
        self.assertEqual(code, 1)
        self.assertIn('cannot review its own work', out)

    def test_default_reviewer_is_the_other_agent(self):
        self.change()
        code, out = self.xr('run', env={'CLAUDECODE': '1'})
        self.assertEqual(code, 0, out)
        self.assertIn('Codex review', out)
        code, out = self.xr('run', env={'CODEX_THREAD_ID': 'abc'})
        self.assertEqual(code, 0, out)
        self.assertIn('Claude Code review', out)
        code, out = self.xr('start')
        self.assertEqual(code, 1)
        self.assertIn('--reviewer', out)

    def test_no_reviews_from_inside_a_reviewer(self):
        self.change()
        code, out = self.xr('start', '--reviewer', 'codex', env={'XREVIEW_ROLE': 'reviewer'})
        self.assertEqual(code, 1)
        self.assertIn('disabled inside a reviewer', out)

    def test_refuses_inside_codex_sandbox(self):
        self.change()
        code, out = self.xr('start', '--reviewer', 'claude', env={'CODEX_SANDBOX_NETWORK_DISABLED': '1'})
        self.assertEqual(code, 1)
        self.assertIn('outside the sandbox', out)

    def test_not_a_git_repo(self):
        plain = self.tmp / 'plain'
        plain.mkdir()
        code, out = self.xr('start', '--reviewer', 'codex', cwd=plain)
        self.assertEqual(code, 1)
        self.assertIn('not inside a git repository', out)

    def test_doctor_broken_sandbox_suggests_diff_only(self):
        code, out = self.xr('doctor', '--reviewer', 'codex', env={'FAKE_SANDBOX': 'broken'})
        self.assertEqual(code, 1, out)
        self.assertIn('"codex": {"tools": "repo"}', out)

    def test_doctor_broken_sandbox_ok_in_repo_mode(self):
        self.write_config(codex={'bin': str(self.tmp / 'fakes/codex'), 'tools': 'repo'})
        code, out = self.xr('doctor', '--reviewer', 'codex', env={'FAKE_SANDBOX': 'broken'})
        self.assertEqual(code, 0, out)
        self.assertIn('repo-tools mode is on', out)
        self.assertIn("read-only repository tools respond", out)

    def test_doctor_broken_sandbox_ok_in_diff_only_mode(self):
        self.write_config(codex={'bin': str(self.tmp / 'fakes/codex'), 'tools': False})
        code, out = self.xr('doctor', '--reviewer', 'codex', env={'FAKE_SANDBOX': 'broken'})
        self.assertEqual(code, 0, out)
        self.assertIn('diff-only mode is on', out)

    def test_tools_setting_must_be_boolean(self):
        self.write_config(codex={'bin': str(self.tmp / 'fakes/codex'), 'tools': 'no'})
        self.change()
        code, out = self.xr('start', '--reviewer', 'codex')
        self.assertEqual(code, 1, out)
        self.assertIn('must be true, "repo" or false', out)

    def test_doctor_with_fakes(self):
        code, out = self.xr('doctor')
        self.assertEqual(code, 0, out)
        self.assertIn('Codex read-only sandbox works', out)
        self.assertIn('Claude Code is logged in', out)


class RepoToolsServer(Base):
    """The MCP server Codex uses in repo mode: it must only ever read, inside the repo."""

    def rpc(self, *requests, root=None):
        lines = ''.join(json.dumps(r) + '\n' for r in requests)
        proc = subprocess.run([sys.executable, str(SCRIPT), '_repo_tools', str(root or self.repo)], input=lines,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]

    def call(self, name, **arguments):
        reply = self.rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                          'params': {'name': name, 'arguments': arguments}})[0]['result']
        return reply['isError'], reply['content'][0]['text']

    def setUp(self):
        super().setUp()
        (self.repo / 'helpers.py').write_text('def helper():\n    return 42\n')
        (self.repo / 'pkg').mkdir()
        (self.repo / 'pkg' / 'mod.py').write_text('from helpers import helper\n' + 'x = 1\n' * 600)
        self.git(self.repo, 'add', '.')
        self.git(self.repo, 'commit', '-qm', 'more files')
        (self.repo / 'new_untracked.py').write_text('NEW = helper()\n')
        (self.repo / '.env').write_text('TOKEN=abc\n')
        (self.repo / 'blob.bin').write_bytes(b'\x00\x01binary')
        outside = self.tmp / 'outside.txt'
        outside.write_text('outside secret\n')
        os.symlink(str(outside), str(self.repo / 'link_out'))

    def test_protocol(self):
        replies = self.rpc(
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25'}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'ping'},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'resources/list'})
        self.assertEqual([r['id'] for r in replies], [1, 2, 3, 4])      # no reply to the notification
        self.assertEqual(replies[0]['result']['protocolVersion'], '2025-11-25')
        self.assertEqual({t['name'] for t in replies[1]['result']['tools']}, {'read_file', 'list_files', 'search', 'git'})
        self.assertTrue(all(t['annotations']['readOnlyHint'] for t in replies[1]['result']['tools']))
        self.assertEqual(replies[3]['error']['code'], -32601)

    def test_reading(self):
        failed, text = self.call('read_file', path='helpers.py')
        self.assertFalse(failed, text)
        self.assertIn('2\t    return 42', text)
        failed, text = self.call('read_file', path='pkg/mod.py', start_line=3, max_lines=5)
        self.assertFalse(failed, text)
        self.assertIn('call again with start_line=8', text)
        failed, text = self.call('read_file', path='new_untracked.py')
        self.assertFalse(failed, text)

    def test_listing_and_search_skip_secrets(self):
        failed, text = self.call('list_files')
        self.assertFalse(failed, text)
        self.assertIn('new_untracked.py', text)
        self.assertIn('pkg/mod.py', text)
        self.assertNotIn('.env', text)
        failed, text = self.call('list_files', path='pkg', glob='*.py')
        self.assertEqual(text.strip(), 'pkg/mod.py')
        failed, text = self.call('search', pattern='helper')
        self.assertIn('helpers.py:1:', text)
        self.assertIn('pkg/mod.py:1:', text)
        failed, text = self.call('search', pattern='TOKEN')
        self.assertEqual(text, '(no matches)')
        failed, text = self.call('search', pattern='(unclosed')
        self.assertTrue(failed)

    def test_git_history_commands(self):
        for command in ('log', 'show'):
            failed, text = self.call('git', command=command)
            self.assertFalse(failed, command + ': ' + text)
        failed, text = self.call('git', command='log', rev='HEAD~1..HEAD')
        self.assertFalse(failed, text)
        failed, text = self.call('git', command='diff', rev='HEAD~1..HEAD')
        self.assertFalse(failed, text)
        self.assertIn('+def helper():', text)
        failed, text = self.call('git', command='show', path='helpers.py')
        self.assertFalse(failed, text)
        self.assertIn('return 42', text)
        failed, text = self.call('git', command='blame', path='helpers.py')
        self.assertTrue(failed)                                   # reads files named in local config
        failed, text = self.call('git', command='diff')          # working tree: already in the prompt
        self.assertTrue(failed)
        self.assertIn('needs a range', text)
        failed, text = self.call('git', command='status')
        self.assertTrue(failed)

    def test_git_filters_never_run(self):
        # A clean filter configured for .py files would run if git touched the working tree.
        marker = self.tmp / 'filter-ran'
        self.git(self.repo, 'config', 'filter.marker.clean', "sh -c 'touch %s; cat'" % marker)
        self.git(self.repo, 'config', 'diff.marker.textconv', "sh -c 'touch %s; cat \"$1\"' --" % marker)
        (self.repo / '.gitattributes').write_text('*.py filter=marker diff=marker\n')
        (self.repo / 'helpers.py').write_text('def helper():\n    return 43\n')
        for name, arguments in (('git', {'command': 'log'}), ('git', {'command': 'show'}),
                                ('git', {'command': 'diff', 'rev': 'HEAD~1..HEAD'}),
                                ('search', {'pattern': 'helper'}), ('read_file', {'path': 'helpers.py'}),
                                ('list_files', {})):
            self.call(name, **arguments)
        self.assertFalse(marker.exists(), 'a git filter ran inside the tool server')

    def test_committed_secrets_stay_hidden(self):
        (self.repo / '.env').write_text('TOKEN=s3cr3t-val\n')
        self.git(self.repo, 'add', '-f', '.env')
        self.git(self.repo, 'commit', '-qm', 'oops, a secret')
        blob = subprocess.run(['git', '-C', str(self.repo), 'rev-parse', 'HEAD:.env'], stdout=subprocess.PIPE,
                              universal_newlines=True).stdout.strip()
        for arguments in ({'command': 'show'}, {'command': 'diff', 'rev': 'HEAD~1..HEAD'},
                          {'command': 'log', 'rev': 'HEAD~1..HEAD'}):
            failed, text = self.call('git', **arguments)
            self.assertNotIn('s3cr3t-val', text, arguments)
        for arguments in ({'command': 'show', 'rev': blob}, {'command': 'show', 'path': '.env'},
                          {'command': 'diff', 'rev': 'HEAD~1..HEAD', 'path': '.env'}):
            failed, text = self.call('git', **arguments)
            self.assertTrue(failed, arguments)
            self.assertNotIn('s3cr3t-val', text, arguments)

    def test_search_does_not_follow_directory_links(self):
        outside = self.tmp / 'outside-dir'
        outside.mkdir()
        (outside / 'conf.txt').write_text('needle outside the repository\n')
        (self.repo / 'linked').mkdir()
        (self.repo / 'linked' / 'conf.txt').write_text('needle inside\n')
        self.git(self.repo, 'add', 'linked/conf.txt')
        self.git(self.repo, 'commit', '-qm', 'tracked file under a directory')
        import shutil as _shutil
        _shutil.rmtree(str(self.repo / 'linked'))
        os.symlink(str(outside), str(self.repo / 'linked'))       # the index still lists linked/conf.txt
        os.symlink(str(self.repo / '.git'), str(self.repo / 'gitlink'))
        failed, text = self.call('search', pattern='needle|repositoryformatversion')
        self.assertNotIn('outside the repository', text)
        self.assertNotIn('repositoryformatversion', text)
        failed, text = self.call('read_file', path='linked/conf.txt')
        self.assertTrue(failed)
        failed, text = self.call('read_file', path='gitlink/config')
        self.assertTrue(failed)

    def test_partial_clone_has_no_history_tools(self):
        self.git(self.repo, 'config', 'extensions.partialclone', 'origin')
        failed, text = self.call('git', command='log')
        self.assertTrue(failed)
        self.assertIn('partial clone', text)
        failed, text = self.call('read_file', path='helpers.py')
        self.assertFalse(failed, text)

    def test_local_git_config_cannot_point_outside(self):
        outside = self.tmp / 'outside-secret.txt'
        outside.write_text('zz-outside-marker-1\n<helper@example.com> zz-outside-marker-2\n')
        self.git(self.repo, 'config', 'blame.ignoreRevsFile', str(outside))
        self.git(self.repo, 'config', 'mailmap.file', str(outside))
        self.git(self.repo, 'config', 'diff.orderFile', str(outside))
        self.git(self.repo, 'config', 'blame.ignoreRevsFile', str(outside))
        for arguments in ({'command': 'log'}, {'command': 'show'}, {'command': 'diff', 'rev': 'HEAD~1..HEAD'}):
            failed, text = self.call('git', **arguments)
            self.assertFalse(failed, arguments)
            self.assertNotIn('zz-outside-marker', text, arguments)

    def test_everything_outside_the_boundary_is_refused(self):
        attempts = [
            ('read_file', {'path': '.env'}, 'credentials'),
            ('read_file', {'path': '../outside.txt'}, 'outside the repository'),
            ('read_file', {'path': 'link_out'}, 'outside the repository'),
            ('read_file', {'path': str(self.tmp / 'outside.txt')}, 'relative'),
            ('read_file', {'path': '.git/config'}, '.git'),
            ('read_file', {'path': 'blob.bin'}, 'binary'),
            ('list_files', {'path': '..'}, 'outside the repository'),
            ('search', {'pattern': 'x', 'path': '../'}, 'outside the repository'),
            ('git', {'command': 'log', 'rev': '--output=/tmp/pwned'}, 'invalid revision'),
            ('git', {'command': 'diff', 'path': '../outside.txt'}, 'outside the repository'),
            ('git', {'command': 'show', 'path': '.git/config'}, 'outside the repository'),
            ('git', {'command': 'show', 'rev': 'HEAD:helpers.py'}, 'invalid revision'),
            ('git', {'command': 'show', 'path': '.env'}, 'credentials'),
            ('git', {'command': 'blame', 'path': 'helpers.py'}, 'unsupported'),
            ('git', {'command': 'push'}, 'unsupported'),
            ('no_such_tool', {}, 'unknown tool'),
        ]
        for name, arguments, message in attempts:
            with self.subTest(name=name, arguments=arguments):
                failed, text = self.call(name, **arguments)
                self.assertTrue(failed, text)
                self.assertIn(message, text)
        self.assertFalse(Path('/tmp/pwned').exists())
        self.assertEqual((self.tmp / 'outside.txt').read_text(), 'outside secret\n')


if __name__ == '__main__':
    unittest.main()
