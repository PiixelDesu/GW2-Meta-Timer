"""Offline GW2 meta timer: reads the community event dataset stored in events.json."""
import json,os,re,time
from datetime import datetime,timezone
from pathlib import Path
import requests

CATEGORIES=('Core Tyria','Heart of Thorns','Path of Fire','End of Dragons','Secrets of the Obscure','Janthir Wilds','Visions of Eternity','Living World','The Icebrood Saga','Public Instances')
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
    if not pattern and not partial:return []
    segs=event.get('segments',[])
    lookup={str(s.get('id',i)):s for i,s in enumerate(segs)} if isinstance(segs,list) else segs
    midnight=now.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    # Partial is the prefix at UTC midnight, followed by the repeating pattern.
    # Partial-only schedules (e.g. Hard world bosses) have no repeating pattern.
    # Repeat their UTC-day prefix for tomorrow so late-day checks still find bosses.
    steps=(partial + pattern*35) if pattern else partial
    cursor=midnight; out=[]
    for item in steps:
        idx=item.get('r');duration=item.get('d')
        if not isinstance(duration,(int,float)) or duration<=0:continue
        segment=lookup.get(str(idx),{})
        if cursor>=now.timestamp()-60 and cursor<now.timestamp()+86400 and segment.get('name'):
            out.append((int(cursor),segment))
        cursor+=duration*60
        if cursor>now.timestamp()+86400:break
    if not pattern:
        # The partial-only dataset can extend beyond midnight. Keep the next
        # day's occurrences in range without duplicating those already listed.
        cursor = midnight + 86400
        for item in partial:
            duration = item.get('d')
            if not isinstance(duration, (int, float)) or duration <= 0:
                continue
            segment = lookup.get(str(item.get('r')), {})
            if cursor >= now.timestamp() and cursor < now.timestamp() + 86400 and segment.get('name'):
                if not any(ts == int(cursor) and seg.get('name') == segment.get('name') for ts, seg in out):
                    out.append((int(cursor), segment))
            cursor += duration * 60
            if cursor >= now.timestamp() + 86400:
                break
    return out

def build(events, now):
    # One next scheduled phase per map/event group, rather than five per expansion.
    # Discord permits at most 10 embeds, each with a 4096-character description.
    groups = {}
    now_ts = now.timestamp()
    for event in events:
        if event.get('active') is False:
            continue
        if event.get('category') == 'Core Tyria' and 'pvp' in str(event.get('name', '')).lower():
            continue
        cat = event.get('category', '')
        if not any(cat.startswith(c) for c in CATEGORIES):
            continue
        valid = []
        for ts, seg in occurrences(event, now):
            name = str(seg.get('name', '')).strip()
            if ts < now_ts or not name or name.lower().startswith(('reset', '(continued)', 'preparations')):
                continue
            valid.append((ts, name, str(seg.get("chatlink") or "").strip()))
        if valid:
            event_id = int(event.get('id', 999999))
            if cat == 'Core Tyria' and event.get('name') in ('World bosses', 'Hard world bosses'):
                # Show every distinct boss's next spawn in the next 24 hours.
                # Keep event-ID ordering, then chronological order within each boss rotation.
                first_by_boss = {}
                for ts, phase, chatlink in valid:
                    first_by_boss.setdefault(phase, (ts, chatlink))
                for phase, (ts, chatlink) in sorted(first_by_boss.items(), key=lambda item: item[1][0]):
                    groups.setdefault(cat, []).append((event_id, ts, event.get('name', 'Map'), phase, chatlink))
            else:
                ts, phase, chatlink = min(valid)
                groups.setdefault(cat, []).append((event_id, ts, event.get('name', 'Map'), phase, chatlink))

    if not groups:
        raise ValueError('No scheduled events found; check events.json')

    # Each category is a field within one embed. Multiple fields fit inside one
    # embed; splitting at Discord's field limits keeps every expansion visible.
    fields = []
    for cat, rows in sorted(groups.items(), key=lambda kv: min(r[0] for r in kv[1])):
        rows.sort(key=lambda r: (r[0], r[1]))
        # Compact single-line rows: event | next spawn | copyable GW2 waypoint.
        # Keep the full phase name and map name, while avoiding repeated long
        # timestamp labels that make Discord embeds appear unnecessarily narrow.
        lines = []
        for event_id, ts, map_name, phase, chatlink in rows:
            name = str(map_name).strip()
            phase = str(phase).strip()
            label = phase if name.lower() == phase.lower() else f'{name} — {phase}'
            waypoint = (f' · `{chatlink}`'
                        if re.fullmatch(r'\[&[A-Za-z0-9+/=]+\]', chatlink) else '')
            # Discord field values are limited to 1024 characters.
            line = f'• **{label}** · <t:{ts}:R> · <t:{ts}:t>{waypoint}'
            if len(line) > 1024:
                # Only truncate exceptionally long labels, never the waypoint.
                overhead = len(line) - len(label)
                label = label[:max(1, 1024 - overhead - 1)] + '…'
                line = f'• **{label}** · <t:{ts}:R> · <t:{ts}:t>{waypoint}'
            lines.append(line)
        # Discord field values max 1024 characters; chunk without dropping rows.
        chunk, chunk_len, part = [], 0, 1
        for line in lines:
            length = len(line) + (1 if chunk else 0)
            if chunk and chunk_len + length > 1024:
                fields.append({'name': cat + (f' (part {part})' if part > 1 else ''),
                               'value': '\n'.join(chunk), 'inline': False})
                part += 1
                chunk, chunk_len = [], 0
            chunk.append(line)
            chunk_len += len(line) + (1 if len(chunk) > 1 else 0)
        if chunk:
            fields.append({'name': cat + (f' (part {part})' if part > 1 else ''),
                           'value': '\n'.join(chunk), 'inline': False})

    # At most 25 fields per embed, 6000 chars combined embed text, 10 embeds.
    embeds = []
    batch, size = [], 0
    for field in fields:
        cost = len(field['name']) + len(field['value'])
        if batch and (len(batch) >= 25 or size + cost > 5500):
            embeds.append({'color': 0x18a999, 'fields': batch})
            batch, size = [], 0
        batch.append(field)
        size += cost
    if batch:
        embeds.append({'color': 0x18a999, 'fields': batch})
    if len(embeds) > 10:
        raise ValueError('Too many event embeds for a single Discord message')
    print(f'Dashboard: {sum(len(v) for v in groups.values())} event groups, '
          f'{len(groups)} categories, {len(embeds)} embeds')
    return {
        'content': f'**Guild Wars 2 — Scheduled Meta Events**\nUpdated <t:{int(now_ts)}:R> · Event | countdown | local time | waypoint\nNext phase per map; all upcoming Core Tyria world bosses. Offline schedule; instance progress may vary.',
        'embeds': embeds,
        'allowed_mentions': {'parse': []}
    }

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
