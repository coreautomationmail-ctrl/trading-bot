import sys, time, pickle
sys.path.insert(0, r"C:\Users\Silvine Raza\projects\vendor\Kronos")
import pandas as pd, torch
from model import Kronos, KronosTokenizer, KronosPredictor
torch.set_num_threads(6)
tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
m = Kronos.from_pretrained("NeoQuasar/Kronos-small")
p = KronosPredictor(m, tok, device="cpu", max_context=512)
df = pickle.load(open("scripts/.cache/m1_SPY_2026.pkl", "rb")).astype("float64")
b = df.resample("5min", label="left", closed="left").agg(
    {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
b = b.between_time("09:30", "15:55")
x = b.iloc[:400].copy(); x["amount"] = x["volume"] * x["close"]
ts = pd.Series(b.index[:400].tz_localize(None))
yts = pd.Series(pd.date_range(ts.iloc[-1] + pd.Timedelta("5min"), periods=6, freq="5min"))
t = time.time()
out = p.predict(df=x.reset_index(drop=True), x_timestamp=ts, y_timestamp=yts, pred_len=6,
                T=1.0, top_p=0.9, sample_count=5, verbose=False)
print("secs", round(time.time() - t, 2))
print(out[["close"]].T)
print("last actual", b.iloc[399]["close"], "next actual", b.iloc[400:406]["close"].values)
