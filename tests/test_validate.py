"""Tests for tools/validate.py, run against a temporary copy of this repository.

Generic on purpose: the same file works in any plugin repository that uses this
validator, because it picks whichever skill the repository has.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class Validate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='plugin-validate-'))
        self.repo = self.tmp / 'repo'
        shutil.copytree(str(ROOT), str(self.repo), symlinks=True,
                        ignore=shutil.ignore_patterns('.git', '__pycache__', 'results'))
        self.skill = sorted(p for p in (self.repo / 'skills').iterdir() if p.is_dir())[0]
        self.git('init', '-q')
        self.git('add', '-A')
        self.git('commit', '-qm', 'baseline')

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def git(self, *args):
        subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t'] + list(args),
                       cwd=str(self.repo), check=True, stdout=subprocess.DEVNULL)

    def validate(self, *args):
        proc = subprocess.run([sys.executable, 'tools/validate.py'] + list(args), cwd=str(self.repo),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        return proc.returncode, proc.stdout

    def edit_json(self, rel, **changes):
        path = self.repo / rel
        data = json.loads(path.read_text())
        data.update(changes)
        path.write_text(json.dumps(data, indent=2) + '\n')

    def version(self):
        return json.loads((self.repo / 'plugin.json').read_text())['version']

    def bump(self, part=2):
        numbers = [int(n) for n in self.version().split('.')]
        numbers[part] += 1
        numbers[part + 1:] = [0] * (2 - part)
        self.edit_json('plugin.json', version='.'.join(map(str, numbers)))

    def touch_skill(self):
        skill_md = self.skill / 'SKILL.md'
        skill_md.write_text(skill_md.read_text() + '\nOne more line.\n')

    def test_repository_passes(self):
        code, out = self.validate()
        self.assertEqual(code, 0, out)

    def test_manifest_drift_fails(self):
        self.edit_json('.claude-plugin/plugin.json', description='Something else')
        code, out = self.validate()
        self.assertEqual(code, 1, out)
        self.assertIn("disagree on 'description'", out)

    def test_skill_change_needs_version_bump(self):
        self.touch_skill()
        code, out = self.validate()
        self.assertEqual(code, 1, out)
        self.assertIn('raise it so Codex users get the update', out)
        self.bump()
        code, out = self.validate()
        self.assertEqual(code, 0, out)

    def test_new_untracked_skill_file_counts_as_a_change(self):
        extra = self.skill / 'references' / 'extra-notes.md'
        extra.parent.mkdir(exist_ok=True)
        extra.write_text('# Extra\n')
        code, out = self.validate()
        self.assertEqual(code, 1, out)
        self.assertIn('extra-notes.md', out)

    def test_lower_version_does_not_count_as_a_bump(self):
        self.touch_skill()
        self.edit_json('plugin.json', version='0.0.0')
        code, out = self.validate()
        self.assertEqual(code, 1, out)

    def test_readme_only_change_needs_no_bump(self):
        readme = self.repo / 'README.md'
        readme.write_text(readme.read_text() + '\nMore docs.\n')
        code, out = self.validate()
        self.assertEqual(code, 0, out)

    def test_base_ref_compares_committed_work(self):
        self.touch_skill()
        self.git('commit', '-qam', 'skill change without bump')
        code, out = self.validate()          # HEAD already contains the change
        self.assertEqual(code, 0, out)
        code, out = self.validate('--base', 'HEAD~1')
        self.assertEqual(code, 1, out)

    def test_broken_link_and_case_without_graders_fail(self):
        notes = self.skill / 'references' / 'broken-link-test.md'
        notes.parent.mkdir(exist_ok=True)
        notes.write_text('[gone](nowhere.md)\n')
        case = self.repo / 'evals' / 'empty-case'
        case.mkdir(parents=True)
        (case / 'prompt.md').write_text('Do something.\n')
        self.bump()
        code, out = self.validate()
        self.assertEqual(code, 1, out)
        self.assertIn('broken link nowhere.md', out)
        self.assertIn('eval case has no graders', out)

    def test_missing_hooks_file_fails(self):
        self.edit_json('.claude-plugin/plugin.json', hooks='./no-such-hooks/hooks.json')
        self.bump()
        code, out = self.validate()
        self.assertEqual(code, 1, out)
        self.assertIn('hooks file no-such-hooks/hooks.json does not exist', out)


if __name__ == '__main__':
    unittest.main()
