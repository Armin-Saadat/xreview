#!/usr/bin/env bash
# Scaffold for an xreview trigger eval: a small git repository with a toy
# experiment. Runs only with `claude plugin eval --scaffold`.
set -euo pipefail
git init -q
git config user.name eval
git config user.email eval@example.com
cat > train.py <<'EOF'
"""Toy experiment: logistic regression trained on train, tuned on val, reported on test."""
import math
import random


def load(seed=0, n=600):
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        x = [rng.gauss(0, 1) for _ in range(3)]
        y = 1 if x[0] + 0.5 * x[1] + rng.gauss(0, 0.5) > 0 else 0
        rows.append((x, y))
    return rows


def split(rows, seed=0):
    rows = rows[:]
    random.Random(seed).shuffle(rows)
    n = len(rows)
    return rows[: int(0.7 * n)], rows[int(0.7 * n): int(0.85 * n)], rows[int(0.85 * n):]


def predict(w, x):
    return 1 / (1 + math.exp(-sum(wi * xi for wi, xi in zip(w, x))))


def accuracy(w, rows):
    return sum((predict(w, x) > 0.5) == bool(y) for x, y in rows) / len(rows)


def train(train_rows, epochs=100, lr=0.1):
    w = [0.0, 0.0, 0.0]
    for _ in range(epochs):
        grad = [0.0, 0.0, 0.0]
        for x, y in train_rows:
            err = predict(w, x) - y
            grad = [g + err * xi for g, xi in zip(grad, x)]
        w = [wi - lr * g / len(train_rows) for wi, g in zip(w, grad)]
    return w


if __name__ == "__main__":
    tr, va, te = split(load())
    w = train(tr)
    print("val", round(accuracy(w, va), 3), "test", round(accuracy(w, te), 3))
EOF
printf '# Toy experiment\n\nRun `python3 train.py` to recieve validation and test accuracy.\n' > README.md
git add -A
git commit -q -m "baseline experiment"

# Uncommitted change under review: standardization fitted on all rows, before the split.
python3 - <<'EOF'
from pathlib import Path
p = Path("train.py")
t = p.read_text()
t = t.replace(
    "def split(rows, seed=0):",
    "def standardize(rows):\n"
    "    cols = list(zip(*[x for x, _ in rows]))\n"
    "    mu = [sum(c) / len(c) for c in cols]\n"
    "    sd = [(sum((v - m) ** 2 for v in c) / len(c)) ** 0.5 for c, m in zip(cols, mu)]\n"
    "    return [([(v - m) / s for v, m, s in zip(x, mu, sd)], y) for x, y in rows]\n"
    "\n\ndef split(rows, seed=0):")
t = t.replace("tr, va, te = split(load())", "tr, va, te = split(standardize(load()))")
p.write_text(t)
EOF
