# ingest

Source-specific importers and mirroring helpers.

Expected first targets:

- `make_datasets`
- `autoresearch`
  - `fetch_runs.py` for viewer `transcript.md?max_output_lines=0` / `run.json` fetches
  - `extract_python_artifacts.py` for transcript parsing helpers and legacy reconstruction fallback
  - `build_judge_xml.py` for optional XML rendering from canonical JSON cases or legacy package manifests
  - `pipeline.py` for the batch `mirror workspace -> fetch transcript if available -> build canonical case -> render XML` flow
