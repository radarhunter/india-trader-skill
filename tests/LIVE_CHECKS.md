# Live checks (run by hand before a release)

Everything below hits real NSE / Yahoo endpoints. Run from a folder where a
`trader/` directory may be created. All times IST.

```bash
S=~/.claude/skills   # or the repo root
python3 $S/nse-alerts/alerts.py market
python3 $S/nse-alerts/alerts.py holidays --refresh
python3 $S/nse-alerts/alerts.py quote RELIANCE TCS INFY
python3 $S/nse-scanner/scan.py universe                       # expect ~500 symbols, a handful ASM/GSM
python3 $S/nse-scanner/scan.py daily --limit 60               # evening / pre-market
python3 $S/nse-scanner/scan.py orb --limit 60                 # 09:35-14:45 on a trading day
python3 $S/nse-news/news.py RELIANCE,TCS --all --hours 72
python3 $S/nse-results/results.py RELIANCE
python3 $S/nse-insider/insider.py insider --days 3 --max-xml 10 --all
python3 $S/nse-insider/insider.py deals
python3 $S/nse-insider/insider.py flows
python3 $S/nse-picks/picks.py tomorrow --limit 80 --confirm 25
python3 $S/nse-exit/exit.py RELIANCE --entry 1170 --stop 1.2% --target 2R --no-write
python3 $S/nse-timemachine/timemachine.py RELIANCE --entry 1170 --stop 1156 --target 1198 --runs 3000 --no-write
```

Endpoints verified on 31 Oct 2026 (from a browser on nseindia.com):

| endpoint | status | shape |
| --- | --- | --- |
| Yahoo `/v8/finance/chart/<SYM>.NS` 1d, 5m (60d), 1m | OK | `chart.result[0].meta/timestamp/indicators.quote[0]` |
| Yahoo `/v7/finance/spark` 20 symbols | OK | `spark.result[].response[0]` closes only |
| Yahoo `<SYM>.BO` | OK | BSE prices |
| Yahoo `/v10/finance/quoteSummary` | 401 | not used |
| NSE `/api/NextApi/apiClient/marketWatchApi?functionName=getIndicesData&symbol=NIFTY 500` | OK | `data.data[]` 495 EQ rows with OHLC |
| NSE same with `SECURITIES IN F&O` | OK | 218 rows |
| NSE `/api/NextApi/apiClient/GetQuoteApi?functionName=getSymbolData&marketType=N&series=EQ&symbol=` | OK | `equityResponse[0].{metaData,priceInfo,tradeInfo,secInfo}`; `priceInfo.priceBand` "lo-hi", `tickSize` |
| NSE `GetQuoteApi?functionName=getMetaData&symbol=` | OK | `isFNOSec`, `isSuspended` |
| NSE `/api/quote-equity` (old) | 403 | dead; not used |
| NSE `/api/equity-stockIndices` (old) | 404 | dead; not used |
| NSE `/api/corporate-announcements?index=equities&symbol=` | OK | list; `desc`, `attchmntText`, `an_dt`, `attchmntFile` |
| NSE `/api/corporate-board-meetings?index=equities&symbol=` | OK | `bm_date`, `bm_desc` (purpose), `bm_purpose` (generic) |
| NSE `/api/results-comparision?symbol=` | OK | `resCmpData[]` with `re_net_sale`, `re_pro_loss_bef_tax`, `re_net_profit`, `re_diluted_eps`, `re_to_dt` |
| NSE `/api/corporates-financial-results` | OK | filing metadata only (no numbers) |
| NSE `/api/holiday-master?type=trading` | OK | `CM[].tradingDate`, `description` |
| NSE `/api/reportASM` | OK | `longterm.data[]`, `shortterm.data[]` with `asmSurvIndicator` |
| NSE `/api/reportGSM` | OK | list with `gsmStage` |
| NSE `/api/corporates-pit-gg?index=equities` | OK | `data[]` filings with `xmlFileName` (XBRL has the trade) |
| NSE `/api/corporates-pit` (old) | empty | not used |
| NSE `/api/snapshot-capital-market-largedeal` | OK | `BULK_DEALS_DATA`, `BLOCK_DEALS_DATA`, `SHORT_DEALS_DATA` |
| NSE `/api/fiidiiTradeReact` | OK | `category`, `date`, `buyValue`, `sellValue`, `netValue` |
| NSE `/api/marketStatus` | OK | `marketState[]` |
| nsearchives CSVs (nifty500, fo_mktlots, bulk, block, EQUITY_L) | not verified from the browser (download links) | used as fallbacks only |
