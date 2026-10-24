"""GW2 wiki-backed Discord meta schedule, updated by GitHub Actions."""
import json, os, re, sys, time
from datetime import datetime, timezone
from urllib.parse import quote
import requests

WIKI = 'https://wiki.guildwars2.com/w/index.php?title=Widget:Event_timer/data.json&action=raw'
CATEGORIES = ('Heart of Thorns','Path of Fire','End of Dragons','Secrets of the Obscure','Janthir Wilds','Visions of Eternity','Living World','The Icebrood Saga','Public Instances')
SESSION = requests.Session()
SESSION.headers['User-Agent'] = 'GW2DiscordMetaDashboard/1.0 (personal Discord dashboard)'

def fetch():
    r=SESSION.get(WIKI,timeout=25);r.raise_for_status()
    data=r.json()
    if not isinstance(data.get('events'),dict): raise ValueError('Wiki event data format changed: expected events object')
    return data['events']

def occurrences(event, now):
    """Repeat wiki pattern from midnight UTC, respecting partial prefix after midnight.
    A pattern is a list of {r:segment index,d:duration in minutes}.
    A partial prefix covers the part of the preceding cycle at 00:00 UTC.
    """
    pattern=event.get('pattern',[])
    partial=event.get('sequences',{}).get('partial',[])
    if not pattern or not isinstance(pattern,list):return []
    # Widget's partial describes the remaining previous cycle after 00:00.
    steps=partial+pattern*30
    midnight=now.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    cursor=midnight
    out=[]
    for item in steps:
        if not isinstance(item,dict):continue
        idx=item.get('r');duration=item.get('d')
        if not isinstance(idx,int) or not isinstance(duration,(int,float)) or duration<=0:continue
        if cursor >= now.timestamp()-60 and cursor < now.timestamp()+86400:
            segments=event.get('segments',{})
            segment=segments.get(str(idx)) if isinstance(segments,dict) else (segments[idx] if isinstance(segments,list) and idx<len(segments) else None)
            if segment and segment.get('name'):
                out.append((int(cursor),segment))
        cursor+=duration*60
    return out

def rows(events,now):
    output=[]
    for key,e in events.items():
        cat=e.get('category','')
        if not any(cat.startswith(c) for c in CATEGORIES):continue
        for ts,segment in occurrences(e,now):
            name=segment.get('name','')
            # Exclude preparation, generic downtime and uninformative phases.
            if not name or name.lower().startswith(('reset','(continued)','preparations')):continue
            output.append((ts,cat,e.get('name',key),name,segment.get('link') or e.get('link') or ''))
    return sorted(output)

def build(events,now):
    entries=rows(events,now)
    if not entries:raise ValueError('No timed events found; refusing to post an empty dashboard')
    # 10 soonest per category; cap Discord embed total and field limits.
    groups={}
    for ts,cat,zone,phase,link in entries:
        if ts<now.timestamp():continue
        groups.setdefault(cat,[])
        if len(groups[cat])>=5:continue
        title=f'{zone} — {phase}'
        title=title.replace('`','')[:110]
        groups[cat].append(f'• **{title}** · <t:{ts}:R> (<t:{ts}:t>)')
    embeds=[]
    for cat,items in groups.items():
        if not items:continue
        # Embed descriptions max 4096 chars.
        embeds.append({'title':cat[:256],'description':'\n'.join(items)[:4000], 'color':0x18a999})
    # Discord max 10 embeds; combine overflow into last embed.
    if len(embeds)>10:
        extra=embeds[9:]
        embeds=embeds[:9]+[{'title':'Other scheduled metas','description':'\n'.join(f'**{x["title"]}**\n{x["description"]}' for x in extra)[:4000],'color':0x18a999}]
    return {'content':'**Guild Wars 2 — Scheduled Meta Events**\nUpdated <t:%d:R> · Times shown in your local timezone.\nSource: https://wiki.guildwars2.com/wiki/Event_timers\n*Schedule estimates; map-instance progress can vary.*'%int(now.timestamp()),'embeds':embeds,'allowed_mentions':{'parse':[]}}

def call(method,url,payload):
    for attempt in range(4):
        r=SESSION.request(method,url,json=payload,timeout=25)
        if r.status_code==429:
            time.sleep(min(float(r.json().get('retry_after',2)),30));continue
        r.raise_for_status();return r
    raise RuntimeError('Discord rate limit retries exhausted')

def main():
    hook=os.environ['DISCORD_WEBHOOK_URL'].rstrip('/')
    if not re.match(r'^https://(?:discord\.com|discordapp\.com)/api/webhooks/\d+/[^/]+$',hook):
        raise ValueError('Invalid Discord webhook URL')
    events=fetch()
    now=datetime.now(timezone.utc)
    payload=build(events,now)
    mid=os.environ.get('DISCORD_MESSAGE_ID','').strip()
    if mid:
        if not mid.isdigit():raise ValueError('DISCORD_MESSAGE_ID must be numeric')
        call('PATCH',hook+'/messages/'+mid,payload)
        print('Updated existing Discord dashboard message.')
    else:
        r=call('POST',hook+'?wait=true',payload)
        print('CREATED_MESSAGE_ID='+str(r.json()['id']))
        print('Add this ID as GitHub Actions secret DISCORD_MESSAGE_ID to prevent new messages.')

if __name__=='__main__':main()
