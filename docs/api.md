# API Reference

## SES Proxy Reference

This note documents the Dutch Postcode-4 values currently associated with the
proxy identities used for robustness/generalization runs. It is reporting
metadata only and is not used by the experiment runtime.

### Suppressed Values

- `-99997` (sometimes rendered as `-99 997`) means `0-4 / geheim`.

### Proxy Identity Mapping

| Proxy identity | Geo (city/region) | Coordinates | Postcode-4 | Source |
|---|---|---|---:|---|
| `res_1` | Amsterdam, NL-NH | `52.3740 N, 4.8897 E` | `1012` | Zipcode Betekenis + IPXO geofeed |
| `res_2` | Amsterdam, NL-NH | `52.3740 N, 4.8897 E` | `1012` | Zipcode Betekenis + IPXO geofeed |
| `res_3` | 's-Gravenzande, NL-ZH (Westland) | `52.0054 N, 4.1664 E` | `2681` | Zipcode Betekenis + IPXO geofeed |

### CBS PC4 Rows

Dataset used: `https://download.cbs.nl/postcode/2025-cbs_pc4_2024_v1.zip`

Filled header used when inspecting the CBS export:

```text
Postcode-4	Totaal	Man	Vrouw	tot 15 jaar	15 tot 25 jaar	25 tot 45 jaar	45 tot 65 jaar	65 jaar en ouder	Geboren in Nederland met een Nederlandse herkomst	Geboren in Nederland met een Europese herkomst (excl. Nederland)	Geboren in Nederland met herkomst buiten Europa	Geboren buiten Nederland met een Europese herkomst (excl. Nederland)	Geboren buiten Nederland met een herkomst buiten Europa	Totaal	Eenpersoons	Meerpersoons zonder kinderen	Eenouder	Tweeouder	Huishoudgrootte	Totaal	voor 1945	1945 tot 1965	1965 tot 1975	1975 tot 1985	1985 tot 1995	1995 tot 2005	2005 tot 2015	2015 en later	Meergezins	Koopwoning	Huurwoning	Huurcoporatie	Niet bewoond	Personen met WW, Bijstand en/of AO uitkering Beneden AOW-leeftijd	Omgevingsadressendichtheid	Stedelijkheid
```

`1012`:

```text
1012	9120	4905	4215	335	1770	4490	1695	825	40	0	10	30	20	6490	4400	1675	165	250	1.4	6075	5050	25	50	135	450	210	110	50	5770	20	80	715	985	385	8654	1
```

`2681`:

```text
2681	14425	7105	7320	2025	1690	3210	4325	3180	80	0	10	10	10	6240	2120	1925	435	1760	2.2	6025	850	995	880	880	875	650	455	445	1580	70	30	1300	200	1120	1356	3
```

`2691` (alternative postcode previously returned by reverse geocoding for the
same coordinates):

```text
2691	14480	7180	7300	2185	1680	3710	3695	3215	80	0	0	10	0	6600	2520	1910	390	1775	2.2	5885	995	1050	1915	775	230	200	450	275	1075	70	30	1200	175	530	1533	2
```

```{eval-rst}

Agent
-----
.. automodule:: agent
   :members:
   :undoc-members:

Database
--------
.. automodule:: db
   :members:
   :undoc-members:

Analysis
--------
.. automodule:: analysis
   :members:
   :undoc-members:

Proxy Manager
-------------
.. automodule:: proxy_manager
   :members:
   :undoc-members:

Config
------
.. automodule:: config
   :members:
   :undoc-members:
```
