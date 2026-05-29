import asyncio
from importlib import import_module
cfg = import_module('config')
from agent import run_agent

# pick one proxy and one site to speed up
trial_id = "debug-quick"
zip_label = list(cfg.PROXIES.keys())[0]
intent = cfg.ACTIVE_INTENT_PROFILES[0]
proxy_url = cfg.PROXIES[zip_label]

# restrict AD_SITES at runtime
cfg.AD_SITES = [cfg.AD_SITES[0]]

async def main():
    obs = await run_agent(trial_id, zip_label, intent, proxy_url, pool=None)
    print('observations:', len(obs))
    for o in obs[:200]:
        print(o.get('ad_url'))

asyncio.run(main())