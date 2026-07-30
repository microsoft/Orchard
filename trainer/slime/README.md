# trainer/slime

Placeholder for the Orchard fork of [slime](https://github.com/THUDM/slime), the
RL training stack used to train agents against `orchard_env` sandboxes.

The fork is not vendored yet. When it lands, this directory will contain:

```
trainer/slime/
├── slime/                 # upstream package (forked)
├── examples/orchard/      # Orchard rollout + reward code
├── scripts/               # training launch scripts
├── LICENSE                # upstream license
└── ORCHARD_CHANGES.md     # every fork-local change vs. upstream
```

Until then, see [`orchard_env/`](../../orchard_env/) for the environment side of
the project.
