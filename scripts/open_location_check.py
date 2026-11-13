"""Место открытия относительно прошлого дня: где формируется value, направленность, правило 80%.

Запуск:  python -m scripts.open_location_check
"""
import numpy as np, pandas as pd
from scripts.stage1_profiles import load_adjusted
from tpo.data import load_config
from tpo.day_type import classify_days
cfg=load_config(); bars,daily,prof,_,tick=load_adjusted('ES',cfg)
d=classify_days(daily,prof,cfg,tick); d=d[d.day_type!='Short'].sort_values('date').reset_index(drop=True)
d['avg']=(d.high-d.low).rolling(20,min_periods=10).mean().shift(1)
per={k:g.sort_values('period').reset_index(drop=True) for k,g in bars.groupby('date')}
rows=[]
for i in range(1,len(d)):
    t,p=d.iloc[i],d.iloc[i-1]
    if np.isnan(t.avg): continue
    o=t.open; g=per[t.date]
    if p.val<=o<=p.vah: loc='1 внутри VA'
    elif p.low<=o<=p.high: loc='2 вне VA, внутри диапазона'
    else: loc='3 вне диапазона'
    up = o>p.vah
    inside_va=lambda x: (x>=p.val)&(x<=p.vah)
    # направленность
    move=(t.close-o)/t.avg
    # ушли от open в сторону открытия (продолжение) или вернулись в VA
    entered_va=bool(((g.low<=p.vah)&(g.high>=p.val)).any())
    close_out_va = not (p.val<=t.close<=p.vah)
    overlap = not (t.val>p.vah or t.vah<p.val)
    # правило 80%: открытие вне VA, 2 периода подряд close внутри VA -> дошли до другого края VA
    r80=np.nan; trig=False
    if loc!='1 внутри VA':
        c=g.close.values; inn=(c>=p.val)&(c<=p.vah)
        k=[j for j in range(1,len(c)) if inn[j] and inn[j-1]]
        if k:
            trig=True; j=k[0]; rest=g.iloc[j+1:]
            r80=bool((rest.low<=p.val).any()) if up else bool((rest.high>=p.vah).any())
    # продолжение в сторону открытия: close дальше от VA, чем open
    cont = float(t.close>o) if up else (float(t.close<o) if o<p.val else np.nan)
    rows.append(dict(loc=loc,abs_move=abs(move),entered_va=entered_va,close_out_va=close_out_va,
                     overlap=overlap,trend=t.day_type in('Trend','Double Distribution'),
                     r80_trig=trig,r80=r80,cont=cont,oneside=t.re_direction in('up','down')))
f=pd.DataFrame(rows)
g=f.groupby('loc')
out=pd.DataFrame({'дней':g.size(),
 '|close-open| / средний range, мед.':g.abs_move.median().round(2),
 'цена заходила в VA прошлого дня %':100*g.entered_va.mean(),
 'close дня вне VA прошлого дня %':100*g.close_out_va.mean(),
 'VA дня перекрывает VA прошлого %':100*g.overlap.mean(),
 'Trend+DD %':100*g.trend.mean(),
})
print(out.round(1).to_string())
o=f[f['loc']!='1 внутри VA']
print('\nОткрытие вне VA: день закрылся дальше от VA, чем open (продолжение) %:')
print((100*o.groupby('loc').cont.mean()).round(1).to_string())
print('база: close выше open в любой день %', round(100*(d.close>d.open).mean(),1))
print('|close-open|/avg квантили по группам'); print(f.groupby('loc').abs_move.quantile([.25,.5,.75]).unstack().round(2))
print('\nПравило 80% (открытие вне VA, 2 периода подряд close внутри VA -> дошли до другого края VA):')
t=o[o.r80_trig]
print(pd.DataFrame({'сработало условие':o.groupby('loc').r80_trig.sum(),'из них дошли до другого края %':(100*t.groupby('loc').r80.mean()).round(1)}).to_string())
