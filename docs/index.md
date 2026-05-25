# Ad Research Experiment Docs

## Research Question

Does ad exposure and advertiser/domain composition differ across proxy
identities when browsing behavior is held constant?

## Hypotheses

- `H0`: Ad exposure is independent of proxy identity when browsing behavior is identical.
- `H1`: Ad exposure differs by proxy identity even when browsing behavior is identical.

## Design Summary

- Proxy identity is the treatment.
- Intent-profile browsing scripts are the controlled behavior.
- Agents run in fresh sessions to reduce carryover.
- Trials are paired across identities to minimize time-skew.
- Analysis focuses on ad counts, domain distribution, and treatment-cell differences.

```{toctree}
:maxdepth: 2
:caption: Reference

methodology_appendix
api
```
