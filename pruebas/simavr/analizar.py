import pandas as pd, numpy as np, json
F=16e6; BIT=F/9600
tx=pd.read_csv('tx_bt.csv'); led=pd.read_csv('leds.csv'); cmd=pd.read_csv('comandos_enviados.csv')
# --- decodificar TX (8N1) a partir de las transiciones ---
c=tx.ciclo.values; v=tx.valor.values
def nivel(t):
    i=np.searchsorted(c,t,side='right')-1
    return v[i] if i>=0 else 1
bytes_=[]; i=0
edges=[(c[k],v[k]) for k in range(len(c))]
t=0; k=0
falls=[c[k] for k in range(1,len(c)) if v[k]==0 and v[k-1]==1]
last_end=0
for f in falls:
    if f<last_end: continue
    b=0
    for bit in range(8):
        b|=int(nivel(f+(1.5+bit)*BIT))<<bit
    bytes_.append((f,b)); last_end=f+9.5*BIT
# agrupar bytes en líneas de respuesta
resp=[]; cur=None
for f,b in bytes_:
    if cur is None: cur={'inicio':f,'texto':''}
    if b in (10,): cur['fin']=f+10*BIT; resp.append(cur); cur=None
    elif b!=13: cur['texto']+=chr(b)
resp=pd.DataFrame(resp)
# --- agrupar comandos en líneas (bytes consecutivos) ---
cmd['gap']=cmd.ciclo_inicio.diff().fillna(1e12)>12*BIT
cmd['linea']=cmd.gap.cumsum()
lineas=cmd.groupby('linea').agg(inicio=('ciclo_inicio','first'),fin=('ciclo_fin','last'),bytes_=('byte',lambda x:''.join(chr(b) for b in x))).reset_index(drop=True)
lineas['comando']=lineas.bytes_.str.replace('\r','').str.replace('\n','').str.strip()
# estado de LED en el tiempo
ledw={}
for p in ['D2','D3']:
    d=led[led.pin==p].sort_values('ciclo'); ledw[p]=(d.ciclo.values,d.valor.values)
def estado(p,t):
    cc,vv=ledw[p]; j=np.searchsorted(cc,t,side='right')-1; return int(vv[j]) if j>=0 else 0
def primer_cambio(t0,t1):
    tt=[]
    for p in ['D2','D3']:
        cc,vv=ledw[p]
        for j in range(1,len(cc)):
            if t0<cc[j]<t1 and vv[j]!=vv[j-1]: tt.append(cc[j]); break
    return min(tt) if tt else None
esperado={'V':(1,0),'R':(0,1),'A':(1,1),'0':(0,0)}
filas=[]; prev=(0,0)
rs=resp[resp.inicio>lineas.inicio.iloc[0]].reset_index(drop=True)
for idx,l in lineas.iterrows():
    nxt=lineas.inicio.iloc[idx+1] if idx+1<len(lineas) else l.fin+0.5*F
    r=rs[(rs.inicio>l.fin)&(rs.inicio<nxt)]
    rr=r.iloc[0] if len(r) else None
    cm=l.comando.upper()
    exp=esperado.get(cm,prev)
    after=(estado('D2',nxt-1),estado('D3',nxt-1))
    ch=primer_cambio(l.fin,nxt)
    valido=cm in esperado
    ok_led=after==exp; ok_msg=(rr is not None) and ((('OK '+cm) in rr.texto) if valido else ('ERROR' in rr.texto))
    filas.append(dict(n=idx+1,enviado=repr(l.bytes_)[1:-1],comando=cm,valido=valido,
        t_envio_ms=round(l.inicio/F*1000,3),
        lat_led_ms=round((ch-l.fin)/F*1000,3) if ch else None,
        lat_resp_ms=round((rr.inicio-l.fin)/F*1000,3) if rr is not None else None,
        dur_resp_ms=round((rr.fin-rr.inicio)/F*1000,2) if rr is not None else None,
        verde=after[0],rojo=after[1],esperado=f'{exp[0]}{exp[1]}',cumple=bool(ok_led and ok_msg),respuesta=rr.texto if rr is not None else ''))
    prev=after
df=pd.DataFrame(filas); df.to_csv('resultados_por_comando.csv',index=False)
print('lineas',len(df),'cumplen',df.cumple.sum())
main=df[df.n<=50]
st=main.groupby('comando',sort=False).agg(rep=('n','size'),cumplen=('cumple','sum'),
  led_media=('lat_led_ms','mean'),led_sd=('lat_led_ms','std'),led_min=('lat_led_ms','min'),led_max=('lat_led_ms','max'),
  resp_media=('lat_resp_ms','mean'),resp_sd=('lat_resp_ms','std'),resp_min=('lat_resp_ms','min'),resp_max=('lat_resp_ms','max'),
  dur_media=('dur_resp_ms','mean')).round(3)
print(st.to_string()); st.to_csv('estadisticas_latencia.csv')
print(df[df.n>50][['enviado','comando','lat_led_ms','lat_resp_ms','verde','rojo','cumple','respuesta']].to_string())
print(df.iloc[0].respuesta); print(df.iloc[4].respuesta)
