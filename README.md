# PuntProof Research Lab — public investigation copy

[Open the cloud investigation interface](https://xsplashzone.github.io/puntproof-research-lab-public/) · [Source inventory](docs/source/manifest.json) · [Download code](docs/source/research-lab-source.zip)

This is the reviewed application code and a read-only copy of the **existing** Research Lab interface, published at the owner's explicit request for ChatGPT testing and investigation. It is not the production PuntProof website. The public UI uses an aggregate local snapshot dated 27 October 2026. It has no connection to the local service and does not execute the Python/V3 model.

At capture: 44,048 validated records, 161 original sealed forecasts, 146 scored forecasts, one completed sequential learning update and two executed V3 component comparisons. The continuous GAMD campaign executed three new experiments, one linked recovery and two scheduled refits: seven model artifacts. Two metadata-only duplicates were skipped before fitting. Coverage eligibility increased from 184 to 244 of the original 783 decisions; all 161 original forecasts remain immutable. An independent future population has 39 separately sealed forecasts. These aggregates are not individual predictions or betting recommendations. None of the three new candidates beat frozen F on matched mean absolute error.

Private model seeds, football payloads, individual forecasts/outcomes, wager records, credentials, source-admission evidence and production configuration are intentionally absent. Full scientific execution requires the existing admitted local repository, original supplied engine and pinned runtime. Source files describe those integration boundaries; they do not grant access.

## Testing the public code

Standard-library arithmetic and persistence checks can run from a fresh checkout:

```sh
PYTHONPATH=tools/research_lab python -m unittest discover -s tests/research_lab -p test_core.py
PYTHONPATH=tools/research_lab python -m unittest discover -s tests/research_lab -p test_monitoring.py
PYTHONPATH=tools/research_lab python -m unittest discover -s tests/research_lab -p test_experiments.py
```

Some integration tests require the private governed repository and supplied engine and cannot run from this intentionally limited export. The hosted interface itself uses the original HTML/CSS/JavaScript with read-only actions and relative paths for GitHub Pages. New challenger fitting is implemented in the private local Research Lab; this static site does not execute it. Scheduled refits and exposed development are separate from prospective evaluation.

Unknown genuine betting ROI remains unmeasured. Repeated exposed development is not blind validation; no public/production promotion or automated wagers are enabled.

Exported application commit: `c88f516b5fffaba6fd729ff0b115f51c321b05e8`. [Exact export custody](docs/source/export-custody.json). Standing local limits: one worker, 12 distinct specifications and 60 compute-minutes per rolling 24 hours, at most 300 seconds and 4096 MiB per attempt. No private Python execution continues when the owner PC is off.
