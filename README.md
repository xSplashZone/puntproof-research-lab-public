# PuntProof Research Lab — public investigation copy

[Open the cloud investigation interface](https://xsplashzone.github.io/puntproof-research-lab-public/) · [Source inventory](docs/source/manifest.json) · [Download code](docs/source/research-lab-source.zip)

This is the reviewed application code and a read-only copy of the **existing** Research Lab interface, published at the owner's explicit request for ChatGPT testing and investigation. It is not the production PuntProof website. The public UI uses an aggregate local snapshot dated 27 October 2026. It has no connection to the local service and does not execute the Python/V3 model.

At capture: 43,801 imported records, 161 sealed forecasts, zero settlements/learned weeks/executed comparisons. Two already-sealed component candidates are prepared only. The final Week 7 DAL–PHI completion marker remains unavailable. Prepared candidates and authored tests are not scientific results.

Private model seeds, football payloads, individual forecasts/outcomes, wager records, credentials, source-admission evidence and production configuration are intentionally absent. Full scientific execution requires the existing admitted local repository, original supplied engine and pinned runtime. Source files describe those integration boundaries; they do not grant access.

## Testing the public code

Standard-library arithmetic and persistence checks can run from a fresh checkout:

```sh
PYTHONPATH=tools/research_lab python -m unittest discover -s tests/research_lab -p test_core.py
PYTHONPATH=tools/research_lab python -m unittest discover -s tests/research_lab -p test_monitoring.py
PYTHONPATH=tools/research_lab python -m unittest discover -s tests/research_lab -p test_experiments.py
```

Some integration tests require the private governed repository and supplied engine and cannot run from this intentionally limited export. The hosted interface itself uses the original HTML/CSS/JavaScript with read-only actions and relative paths for GitHub Pages. No new predictive model was built.

Unknown genuine betting ROI remains unmeasured. Repeated exposed development is not blind validation; no public/production promotion or automated wagers are enabled.
