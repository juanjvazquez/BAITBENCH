# BAITBENCH statistical analyses

This project contains the rebuttal and camera-ready analyses layered on the
canonical cases and judgments in `../reward-hacking-evals/`.

The primary entry point is `run_camera_ready_analyses.py`. It runs 21 analysis
tasks, regenerates nine paper-table views, and checks 25 expected scientific
invariants. It does not call model providers or modify external services.

From the repository root:

```bash
python3 reproduce.py --quick
python3 reproduce.py
```

Outputs are written to `analyze_transcripts/generated/reproduction/` by the
root command. The checked-in `generated/camera_ready/` directory contains the
reference results used during the camera-ready revision.

See `CAMERA_READY_REPRODUCTION.md` for the analyses and denominator details.
