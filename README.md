# PuntProof Research Lab — public investigation copy

[Open the investigation interface](https://xsplashzone.github.io/puntproof-research-lab-public/) · [Source inventory](docs/source/manifest.json) · [Download code](docs/source/research-lab-source.zip)

This is the reviewed application code and a read-only copy of the existing Research Lab interface, published at the owner’s request for testing and investigation. It is separate from public PuntProof production. Snapshot captured **2026-10-27T22:48:24.3289546+10:00**; exported application commit `cdb75fcc21047c4acb06db0dd434b81ea927df55`. [Exact code custody](docs/source/export-custody.json).

The local application reports 44,048 validated records, 161 original sealed forecasts, 146 settled forecasts and 1 saved sequential learning updates. Those counts are retained local work, not activity performed by this public site.

Historical coverage: 184 to 244 eligible decisions out of 783; 161 original forecasts remain immutable. The retained-field repair addressed 392 missing fields across 65 priority cases. The independent GAMD history supports 451 decisions; the fixed prior-workload subgroup has 83/115 eligible cases. These are distinct denominators, not additional forecasts. The 184-to-244 eligibility gain preceded this foundation repair; eligibility remained 244 during this repair.

The forward store contains 1915 cumulative immutable forecast records across 14 games, including preserved prior revisions. The retained batch snapshot itself reports 1889 before its separate linked recovery; that historical count and failure remain intact. The latest repair issued 938 forecasts in 56 new seals and correctly reused 0 existing seals (0 forecasts) with exactly identical numerical inputs, membership and model. New plus reused seals cover all 56 current horizon jobs. Reuse does not count as new forecasting or learning. The original batch timeout remains visible; a separate verified same-input recovery completed that one job without a fit or learning update. Future membership/availability uncertainty remains explicit. All three repaired context features are now known for the 469 targets, but all 938 matching saved-model outputs are numerically identical to their earlier versions. This is a verified data repair, not a demonstrated future prediction improvement.

The controlled foundation HGB comparison used unchanged chronological training/calibration/evaluation splits and exactly matched observations against the saved frozen F forecasts. Both channels remain worse than F on mean absolute error and CRPS; a completed fit is not evidence of improvement. No historical F replay was performed.

| Channel | Matched observations | Candidate MAE | Frozen F MAE | Candidate CRPS | Frozen F CRPS |
|---|---:|---:|---:|---:|---:|
| receiving | 3301 | 18.162466 | 17.609018 | 12.995426 | 12.529687 |
| rushing | 999 | 22.213058 | 21.228679 | 15.915346 | 14.797031 |

Mean and median error, probability calibration and economic outcomes are separate objectives. The distributions remain experimental, with known zero/tail shortcomings. Genuine betting ROI is **not established**; no prices, wagers or returns are invented. The two controlled HGB attempts remain consumed. The forward context repair did not refit them or alter their historical results.

Private engine seeds, source payloads, player-level forecasts/outcomes, wagers, credentials, admission records and production configuration are excluded. Full integration requires the admitted local repository, original engine and pinned numerical runtime. The source describes these boundaries but grants no access.

## Testing this exported code

These portable standard-library tests use only the exported application code and authored temporary data. They do not demonstrate model improvement or source correctness. From this public checkout:

```sh
python -I -B -S -c "import pathlib,sys,unittest; p=pathlib.Path.cwd(); sys.path[:0]=[str(p/'tools/research_lab'),str(p/'tests/research_lab')]; r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(['test_core','test_monitoring','test_experiments'])); sys.exit(not r.wasSuccessful())"
```

Other integration tests require the private data, engine and source permissions. The hosted UI uses the existing HTML/CSS/JavaScript with a sanitized aggregate snapshot and disabled local operations. It does not run Python, collect data, train, predict, settle, learn or monitor while the owner PC is off. It has no local service connection. There is no cloud research worker, production promotion or automated wagering here.
