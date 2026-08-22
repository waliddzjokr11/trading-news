"""
price_fetcher.py — CoinGecko primary, Binance + CoinCap fallback, chunked for 100 coins.
Never crashes; returns dict coin_id -> {price, volume, change_24h, source}
"""
import time
import requests
import logging

logger = logging.getLogger(__name__)

# CoinGecko ID -> Binance symbol map (for fallback). Only majors have Binance USDT pairs.
COINGECKO_TO_BINANCE = {
    "bitcoin": "BTCUSDT", "ethereum": "ETHUSDT", "binancecoin": "BNBUSDT", "ripple": "XRPUSDT",
    "solana": "SOLUSDT", "tron": "TRXUSDT", "cardano": "ADAUSDT", "avalanche-2": "AVAXUSDT",
    "toncoin": "TONUSDT", "chainlink": "LINKUSDT", "polkadot": "DOTUSDT", "cosmos": "ATOMUSDT",
    "near": "NEARUSDT", "internet-computer": "ICPUSDT", "hedera-hashgraph": "HBARUSDT",
    "aptos": "APTUSDT", "sui": "SUIUSDT", "sei-network": "SEIUSDT", "celestia": "TIAUSDT",
    "kaspa": "KASUSDT", "optimism": "OPUSDT", "arbitrum": "ARBUSDT", "starknet": "STRKUSDT",
    "mantle": "MNTUSDT", "immutable": "IMXUSDT", "manta-network": "MANTAUSDT", "altlayer": "ALTUSDT",
    "polygon-ecosystem-token": "POLUSDT", "ethereum-classic": "ETCUSDT", "litecoin": "LTCUSDT",
    "bitcoin-cash": "BCHUSDT", "stellar": "XLMUSDT", "vechain": "VETUSDT", "algorand": "ALGOUSDT",
    "eos": "EOSUSDT", "tezos": "XTZUSDT", "neo": "NEOUSDT", "iota": "IOTAUSDT", "kava": "KAVAUSDT",
    "multiversx": "EGLDUSDT", "thorchain": "RUNEUSDT",     "fantom": "FTMUSDT", "cronos": "CROUSDT", "crypto-com-chain": "CROUSDT",
    "flow": "FLOWUSDT", "axelar": "AXLUSDT", "band-protocol": "BANDUSDT", "api3": "API3USDT",
    "uma": "UMAUSDT", "skale": "SKLUSDT", "cartesi": "CTSIUSDT", "bittensor": "TAOUSDT",
    "render-token": "RNDRUSDT", "fetch-ai": "FETUSDT", "singularitynet": "AGIXUSDT",
    "ocean-protocol": "OCEANUSDT", "filecoin": "FILUSDT", "arweave": "ARUSDT", "the-graph": "GRTUSDT",
    "akash-network": "AKTUSDT", "helium": "HNTUSDT", "jasmycoin": "JASMYUSDT", "worldcoin": "WLDUSDT",
    "arkham": "ARKMUSDT", "jupiter": "JUPUSDT", "jito": "JTOUSDT", "pyth-network": "PYTHUSDT",
    "wormhole": "WUSDT", "layerzero": "ZROUSDT", "eigenlayer": "EIGENUSDT", "ethena": "ENAUSDT",
    "chiliz": "CHZUSDT", "gala": "GALAUSDT", "the-sandbox": "SANDUSDT", "sandbox": "SANDUSDT", "axie-infinity": "AXSUSDT",
    "enjincoin": "ENJUSDT", "decentraland": "MANAUSDT", "apecoin": "APEUSDT", "blur": "BLURUSDT",
    "beam": "BEAMUSDT", "dydx": "DYDXUSDT", "lisk": "LSKUSDT", "wavestech": "WAVESUSDT",
    "zilliqa": "ZILUSDT", "harmony": "ONEUSDT", "qtum": "QTUMUSDT", "theta-token": "THETAUSDT",
    "ondo-finance": "ONDOUSDT", "pendle": "PENDLEUSDT", "ether-fi": "ETHFIUSDT",
    "injective-protocol": "INJUSDT", "uniswap": "UNIUSDT", "curve-dao-token": "CRVUSDT",
    "1inch": "1INCHUSDT", "mina-protocol": "MINAUSDT", "celo": "CELOUSDT",
    "oasis-network": "ROSEUSDT", "notcoin": "NOTUSDT", "moonbeam": "GLMRUSDT",
    "astar": "ASTRUSDT", "dogecoin": "DOGEUSDT", "worldcoin-wld": "WLDUSDT", "worldcoin": "WLDUSDT",
    "jito-governance-token": "JTOUSDT", "jito": "JTOUSDT", "waves": "WAVESUSDT", "wavestech": "WAVESUSDT",
    "manta-network": "MANTAUSDT", "toncoin": "TONUSDT",
}

COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"
BINANCE_URL = "https://api.binance.com/api/v3/ticker/24hr"
COINCAP_URL = "https://api.coincap.io/v2/assets"

HEADERS = {"User-Agent": "crypto-alert-system/1.0"}


def _handle_429(resp):
    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
        try:
            wait = int(retry_after)
        except Exception:
            wait = 60
        logger.warning(f"429 rate-limited, wait {wait}s")
        time.sleep(min(wait, 60))
        return True
    return False


def fetch_coingecko(ids, chunk_size=50):
    """Fetch from CoinGecko in chunks. Returns dict or None on failure."""
    # dedupe while preserving order
    seen = set()
    uniq = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    results = {}
    try:
        for idx in range(0, len(uniq), chunk_size):
            chunk = uniq[idx: idx + chunk_size]
            params = {
                "vs_currency": "usd",
                "ids": ",".join(chunk),
                "price_change_percentage": "24h",
                "per_page": len(chunk),
                "page": 1,
            }
            resp = requests.get(COINGECKO_URL, params=params, headers=HEADERS, timeout=15)
            if _handle_429(resp):
                # retry once
                resp = requests.get(COINGECKO_URL, params=params, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"CoinGecko chunk {idx//chunk_size+1} failed {resp.status_code}: {resp.text[:200]}")
                return None
            data = resp.json()
            for item in data:
                cid = item.get("id")
                results[cid] = {
                    "price": item.get("current_price"),
                    "volume": item.get("total_volume"),
                    "change_24h": item.get("price_change_percentage_24h"),
                    "source": "coingecko",
                }
            # be nice to rate limit — CoinGecko 30/min, 100 coins = 2 calls, need 6s gap to stay safe
            time.sleep(6)
        logger.info(f"CoinGecko: fetched {len(results)}/{len(uniq)} coins")
        return results
    except requests.exceptions.RequestException as e:
        logger.warning(f"CoinGecko exception: {e}")
        return None


def fetch_binance(ids):
    """Fallback: Binance 24hr ticker per symbol."""
    results = {}
    try:
        # Binance supports multiple symbols via ?symbols=[...] but public endpoint is single; do batched multi-call
        # Use single call for all symbols if possible: /api/v3/ticker/24hr?symbols=["BTCUSDT",...]
        symbols = [COINGECKO_TO_BINANCE.get(cid) for cid in ids if COINGECKO_TO_BINANCE.get(cid)]
        if not symbols:
            return None
        # Binance symbols param requires JSON array string
        import json as _json
        # Try bulk endpoint first
        try:
            resp = requests.get(BINANCE_URL, params={"symbols": _json.dumps(symbols)}, headers=HEADERS, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                # map back
                rev = {v: k for k, v in COINGECKO_TO_BINANCE.items()}
                for item in data:
                    sym = item.get("symbol")
                    cid = rev.get(sym)
                    if cid:
                        results[cid] = {
                            "price": float(item.get("lastPrice", 0)),
                            "volume": float(item.get("quoteVolume", 0)),
                            "change_24h": float(item.get("priceChangePercent", 0)),
                            "source": "binance",
                        }
                logger.info(f"Binance bulk: fetched {len(results)}/{len(ids)}")
                return results if results else None
        except Exception:
            pass
        # Fallback single calls (rate limited but 100 coins = 100 calls; do with small delay)
        for cid in ids:
            sym = COINGECKO_TO_BINANCE.get(cid)
            if not sym:
                continue
            try:
                r = requests.get(BINANCE_URL, params={"symbol": sym}, headers=HEADERS, timeout=10)
                if r.status_code == 200:
                    j = r.json()
                    results[cid] = {
                        "price": float(j.get("lastPrice", 0)),
                        "volume": float(j.get("quoteVolume", 0)),
                        "change_24h": float(j.get("priceChangePercent", 0)),
                        "source": "binance",
                    }
                time.sleep(0.2)
            except Exception as e:
                logger.debug(f"Binance {sym} fail: {e}")
        return results if results else None
    except requests.exceptions.RequestException as e:
        logger.warning(f"Binance exception: {e}")
        return None


def fetch_coincap(ids):
    """Second fallback: CoinCap assets."""
    results = {}
    try:
        # CoinCap id mapping differs (e.g., bitcoin -> bitcoin). Try direct.
        resp = requests.get(COINCAP_URL, params={"limit": 2000}, headers=HEADERS, timeout=15)
        if _handle_429(resp):
            resp = requests.get(COINCAP_URL, params={"limit": 2000}, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"CoinCap failed {resp.status_code}")
            return None
        data = resp.json().get("data", [])
        # build lookup by id
        lookup = {item["id"]: item for item in data}
        # also by symbol lower
        sym_lookup = {item["symbol"].lower(): item for item in data}
        for cid in ids:
            # try direct id
            item = lookup.get(cid) or sym_lookup.get(cid.split("-")[0])
            # also try binance symbol base
            if not item:
                sym = COINGECKO_TO_BINANCE.get(cid, "").replace("USDT", "").lower()
                item = sym_lookup.get(sym)
            if item:
                results[cid] = {
                    "price": float(item.get("priceUsd", 0)),
                    "volume": float(item.get("volumeUsd24Hr", 0)),
                    "change_24h": float(item.get("changePercent24Hr", 0)),
                    "source": "coincap",
                }
        logger.info(f"CoinCap: fetched {len(results)}/{len(ids)}")
        return results if results else None
    except Exception as e:
        logger.warning(f"CoinCap exception: {e}")
        return None


def fetch_prices(watchlist, primary="coingecko", chunk_size=50):
    """
    Priority chain: coingecko -> binance -> coincap
    Returns: (prices_dict, source_used)
    Never raises.
    """
    # dedupe watchlist
    seen = set()
    wl = []
    for c in watchlist:
        if c not in seen:
            seen.add(c)
            wl.append(c)
    # 1) CoinGecko
    if primary == "coingecko":
        res = fetch_coingecko(wl, chunk_size=chunk_size)
        if res and len(res) >= max(1, len(wl) * 0.5):  # at least half
            # supplement missing via Binance if partial
            if len(res) < len(wl):
                missing = [c for c in wl if c not in res]
                bin_fill = fetch_binance(missing)
                if bin_fill:
                    merged = {**res, **bin_fill}
                    logger.info(f"CoinGecko {len(res)} + Binance supplement {len(bin_fill)} -> {len(merged)}/{len(wl)}")
                    return merged, "coingecko+binance"
            return res, "coingecko"
        logger.warning("CoinGecko incomplete/failed, trying Binance")
    # 2) Binance
    res2 = fetch_binance(wl)
    if res2 and len(res2) >= max(1, len(wl) * 0.5):
        return res2, "binance"
    logger.warning("Binance incomplete/failed, trying CoinCap")
    # 3) CoinCap
    res3 = fetch_coincap(wl)
    if res3:
        return res3, "coincap"
    # if all fail, return whatever we have (maybe partial)
    best = res or res2 or res3 or {}
    if best:
        src = "coingecko" if res else "binance" if res2 else "coincap" if res3 else "none"
        return best, src + "_partial"
    return {}, "none"

