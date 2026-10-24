"""Offline GW2 meta timer: reads the community event dataset stored in events.json."""
import json,os,re,time
from datetime import datetime,timezone
from pathlib import Path
import requests

CATEGORIES=('Heart of Thorns','Path of Fire','End of Dragons','Secrets of the Obscure','Janthir Wilds','Visions of Eternity','Living World','The Icebrood Saga','Public Instances')
SESSION=requests.Session()
SESSION.headers['User-Agent']='GW2DiscordOfflineMeta/2.0'

def fetch():
    data=json.loads(Path(__file__).with_name('events.json').read_text(encoding='utf-8'))
    if isinstance(data,dict) and isinstance(data.get('events'),list): data=data['events']
    elif isinstance(data,dict) and isinstance(data.get('events'),dict): data=list(data['events'].values())
    if not isinstance(data,list) or not data:
        raise ValueError('events.json is empty. See README to import the community schedule.')
    return data

def occurrences(event,now):
    seq=event.get('sequences',{})
    pattern=seq.get('pattern',event.get('pattern',[]))
    partial=seq.get('partial',[])
    if not pattern:return []
    segs=event.get('segments',[])
    lookup={str(s.get('id',i)):s for i,s in enumerate(segs)} if isinstance(segs,list) else segs
    midnight=now.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    # Partial is the prefix at UTC midnight, followed by the repeating pattern.
    steps=partial+pattern*35
    cursor=midnight; out=[]
    for item in steps:
        idx=item.get('r');duration=item.get('d')
        if not isinstance(duration,(int,float)) or duration<=0:continue
        segment=lookup.get(str(idx),{})
        if cursor>=now.timestamp()-60 and cursor<now.timestamp()+86400 and segment.get('name'):
            out.append((int(cursor),segment))
        cursor+=duration*60
        if cursor>now.timestamp()+86400:break
    return out

def build(events,now):
    groups={}
    for event in events:
        if event.get('active') is False:continue
        cat=event.get('category','')
        if not any(cat.startswith(c) for c in CATEGORIES):continue
        for ts,seg in occurrences(event,now):
            if ts<now.timestamp():continue
            name=seg.get('name','').strip()
            if not name or name.lower().startswith(('reset','(continued)','preparations')):continue
            groups.setdefault(cat,[]).append((ts,f"{event.get('name','Map')} — {name}"))
    if not groups:raise ValueError('No scheduled events found; check events.json')
    embeds=[]
    for cat,rows in sorted(groups.items()):
        rows=sorted(rows)[:5]
        desc='\n'.join(f'• **{name[:110]}** · <t:{ts}:R> (<t:{ts}:t>)' for ts,name in rows)
        embeds.append({'title':cat[:256],'description':desc[:4000],'color':0x18a999})
    if len(embeds)>10:
        overflow=embeds[9:]
        embeds=embeds[:9]+[{'title':'Other metas','description':'\n'.join(f"**{e['title']}**\n{e['description']}" for e in overflow)[:4000],'color':0x18a999}]
    return {'content':f'**Guild Wars 2 — Scheduled Meta Events**\nUpdated <t:{int(now.timestamp())}:R> · Local timezone timestamps\nOffline schedule from community dataset; instance progress may vary.', 'embeds':embeds,'allowed_mentions':{'parse':[]}}

def call(method,url,payload):
    for _ in range(4):
        r=SESSION.request(method,url,json=payload,timeout=25)
        if r.status_code==429:
            time.sleep(min(float(r.json().get('retry_after',2)),30));continue
        r.raise_for_status();return r
    raise RuntimeError('Discord rate-limit retries exhausted')

def main():
    events=fetch()
    payload=build(events,datetime.now(timezone.utc))
    hook=os.environ['DISCORD_WEBHOOK_URL'].rstrip('/')
    if not re.fullmatch(r'https://(?:discord\.com|discordapp\.com)/api/webhooks/\d+/[^/]+',hook):
        raise ValueError('Invalid Discord webhook URL')
    mid=os.environ.get('DISCORD_MESSAGE_ID','').strip()
    if mid:
        if not mid.isdigit():raise ValueError('DISCORD_MESSAGE_ID must be numeric')
        call('PATCH',hook+'/messages/'+mid,payload)
        print('Updated existing Discord dashboard message.')
    else:
        r=call('POST',hook+'?wait=true',payload)
        print('CREATED_MESSAGE_ID='+str(r.json()['id']))

if __name__=='__main__':main()
