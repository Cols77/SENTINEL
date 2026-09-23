"""Build original SVG interface graphics and exact fixture projections."""
from pathlib import Path
import json
import shutil
from html import escape

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'assets/sentinel-ui'
ICONS={
 'drone':'<rect x="8" y="8" width="8" height="8" rx="2"/><path d="m9 9-4-4m10 4 4-4M9 15l-4 4m10-4 4 4"/><circle cx="4.5" cy="4.5" r="2.5"/><circle cx="19.5" cy="4.5" r="2.5"/><circle cx="4.5" cy="19.5" r="2.5"/><circle cx="19.5" cy="19.5" r="2.5"/>',
 'battery':'<rect x="2" y="6" width="17" height="12" rx="2"/><path d="M22 10v4M6 10v4m4-4v4m4-4v4"/>',
 'connected':'<path d="M3 9a14 14 0 0 1 18 0M6 13a9 9 0 0 1 12 0m-9 4a4 4 0 0 1 6 0"/><circle cx="12" cy="20" r=".5" fill="currentColor"/>',
 'disconnected':'<path d="m3 3 18 18M3 9a14 14 0 0 1 3-2m4-1a14 14 0 0 1 11 3M6 13a9 9 0 0 1 4-2m5 1 3 1m-9 4a4 4 0 0 1 6 0"/><circle cx="12" cy="20" r=".5" fill="currentColor"/>',
 'camera':'<path d="M3 7h4l2-3h6l2 3h4v13H3Z"/><circle cx="12" cy="13" r="4"/>',
 'camera-off':'<path d="m2 2 20 20M7 7l2-3h6l2 3h4v9M3 7v13h14m-8-10a4 4 0 0 0 5 6"/>',
 'marker':'<path d="M3 8V3h5m8 0h5v5M3 16v5h5m8 0h5v-5"/><rect x="8" y="8" width="8" height="8" rx="1"/>',
 'warning':'<path d="m12 3 10 18H2Z M12 9v5m0 3v.2"/>',
 'emergency-stop':'<path d="M8 2h8l6 6v8l-6 6H8l-6-6V8Z"/><rect x="8" y="8" width="8" height="8" rx="1"/>',
 'approve':'<path d="m4 12 5 5L20 6"/>',
 'reject':'<path d="m6 6 12 12M18 6 6 18"/>',
 'event':'<path d="M8 5h13M8 12h13M8 19h13"/><circle cx="3" cy="5" r="1"/><circle cx="3" cy="12" r="1"/><circle cx="3" cy="19" r="1"/>',
 'clock':'<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
 'information':'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.2"/>',
}
STATUSES=['UNSURVEYED','SURVEYING','SURVEYED','HAZARD_PENDING','BLOCKED']
STATUS_LABELS={'UNSURVEYED':'Not surveyed','SURVEYING':'Surveying','SURVEYED':'Surveyed','HAZARD_PENDING':'Hazard pending','BLOCKED':'Blocked'}
CSS='''
.survey-zone polygon {fill:#1B2733;stroke:#8296A8;stroke-width:.65;vector-effect:non-scaling-stroke}
.survey-zone[data-status="SURVEYING"] polygon {fill:#213F43;stroke:#47D7C8}
.survey-zone[data-status="SURVEYED"] polygon {fill:#254C45;stroke:#47D7C8}
.survey-zone[data-status="HAZARD_PENDING"] polygon {fill:#473B25;stroke:#F2BA55}
.survey-zone[data-status="BLOCKED"] polygon {fill:#492E35;stroke:#F06565}
.survey-zone.is-selected polygon {stroke:#F3F6F8;stroke-width:2.5}
.survey-zone:focus {outline:none}
.survey-zone:focus-visible polygon {stroke:#47D7C8;stroke-width:3;stroke-dasharray:5 2}
.zone-name {fill:#F3F6F8;font:4.3px system-ui,sans-serif;pointer-events:none}
.zone-status {fill:#A9BAC8;font:3px system-ui,sans-serif;pointer-events:none}
'''.strip()


def write(name,text):
    p=OUT/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text+'\n')


def icon(name):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" role="img" aria-label="{escape(name.replace("-"," "))}">{ICONS[name]}</svg>'


def schematic(fixture,route=False,status='UNSURVEYED'):
    active={'LEFT_ALLEY_MIDDLE','RIGHT_ALLEY_MIDDLE'} if route else {s['section_id'] for s in fixture['sections']}
    groups=[]
    for section in fixture['sections']:
        name=section['section_id']
        if name not in active: continue
        points=section['polygon'];xs=[p[0] for p in points];ys=[p[1] for p in points]
        cx=(min(xs)+max(xs))/2;cy=(min(ys)+max(ys))/2
        label=section['name'].replace(' Alley','')
        side,position=label.split(' ',1)
        groups.append(f'<g id="{name}" class="survey-zone" data-label="{label}" data-status="{status}" role="button" tabindex="0" aria-pressed="false" aria-label="{label}: {STATUS_LABELS[status]}"><title>{name}</title>'
            f'<polygon points="'+ ' '.join(f'{x},{y}' for x,y in points)+'"/>'
            f'<text class="zone-name" x="{cx:g}" y="{cy-5:g}" text-anchor="middle"><tspan x="{cx:g}">{side}</tspan><tspan x="{cx:g}" dy="5">{position}</tspan></text>'
            f'<text class="zone-status" x="{cx:g}" y="{cy+7:g}" text-anchor="middle">{STATUS_LABELS[status]}</text></g>')
    title='Route-only middle zones' if route else 'Six-zone indoor alley'
    mode='ROUTE_ONLY_2_ZONE' if route else 'MARKER_VERIFIED'
    mode_text='route-only, not visually verified' if route else 'Indoor alley · schematic, not to scale'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" role="group" aria-label="{title}" data-mode="{mode}">'
        f'<style>{CSS}</style><text x="50" y="5" text-anchor="middle" fill="#A9BAC8" font-family="system-ui,sans-serif" font-size="2.7">{title}</text>'
        +''.join(groups)+f'<text x="50" y="52" text-anchor="middle" fill="#A9BAC8" font-family="system-ui,sans-serif" font-size="2.6">{mode_text}</text></svg>')


def placeholder(title,description,symbol):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 360" role="img" aria-labelledby="title desc">'
        f'<title id="title">{title}</title><desc id="desc">{description}</desc>'
        '<rect x="1" y="1" width="638" height="358" rx="12" fill="#1B2733" stroke="#8296A8" stroke-dasharray="5 5"/>'
        f'<g transform="translate(296 102) scale(2)" fill="none" stroke="#A9BAC8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{ICONS[symbol]}</g>'
        f'<text x="320" y="209" text-anchor="middle" fill="#F3F6F8" font-size="24" font-family="system-ui,sans-serif">{title}</text>'
        f'<text x="320" y="242" text-anchor="middle" fill="#A9BAC8" font-size="16" font-family="system-ui,sans-serif">{description}</text></svg>')


def build():
    fixture_path=ROOT/'docs/superpowers/specs/contracts/mountain-v0.fixture.json'
    fixture=json.loads(fixture_path.read_text())
    for name in ICONS:write('icons/'+name+'.svg',icon(name))
    write('schematics/zone-states.css',CSS)
    write('schematics/alley-six-zone.svg',schematic(fixture))
    write('schematics/alley-route-only.svg',schematic(fixture,True))
    for status in STATUSES:write('schematics/examples/'+status.lower()+'.svg',schematic(fixture,status=status))
    shutil.copyfile(fixture_path,OUT/'schematics/source-fixture.json')
    for name,title,desc,symbol in [
        ('camera-connecting','Connecting camera','Waiting for a current frame','camera'),
        ('camera-unavailable','Video unavailable','No current frame','camera-off'),
        ('evidence-empty','No evidence yet','No capture is linked to this selection','information'),
        ('evidence-unavailable','Evidence unavailable','The requested capture cannot be shown','warning')]:
        write('placeholders/'+name+'.svg',placeholder(title,desc,symbol))
    write('overlays/video-degraded.svg','<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 56" role="img" aria-label="Video degraded: stream quality reduced"><rect width="640" height="56" rx="8" fill="#F2BA55"/><text x="24" y="35" font-family="system-ui,sans-serif" font-size="18" fill="#101820">VIDEO DEGRADED · Stream quality reduced</text></svg>')
    write('overlays/marker-box.svg','<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 160" fill="none" role="img" aria-label="Marker bounding-box treatment"><rect x="2" y="2" width="236" height="156" rx="4" stroke="#47D7C8" stroke-width="2"/><path d="M2 28V2h26m184 0h26v26M2 132v26h26m184 0h26v-26" stroke="#47D7C8" stroke-width="4"/></svg>')
    write('overlays/marker-label-example.svg','<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 340 64" role="img" aria-label="Illustrative marker label, example marker"><rect width="340" height="64" rx="8" fill="#101820" stroke="#47D7C8"/><text x="16" y="27" fill="#47D7C8" font-family="system-ui,sans-serif" font-size="16">Marker detected · EXAMPLE_MARKER</text><text x="16" y="49" fill="#A9BAC8" font-family="system-ui,sans-serif" font-size="12">ILLUSTRATIVE LABEL · NOT A PHYSICAL MARKER</text></svg>')
    write('overlays/pose-readout-example.svg','<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 160" role="img" aria-label="Illustrative pose display with unavailable values"><rect x="1" y="1" width="418" height="158" rx="8" fill="#1B2733" stroke="#8296A8"/><g font-family="system-ui,sans-serif"><text x="20" y="32" fill="#F3F6F8" font-size="18">Relative pose</text><text x="20" y="65" fill="#A9BAC8" font-size="16">X — m    Y — m    Z — m</text><text x="20" y="96" fill="#A9BAC8" font-size="16">Yaw —°    Score unavailable</text><text x="20" y="132" fill="#A9BAC8" font-size="13">Illustrative · show only supported provider values</text></g></svg>')
    print('Created 14 icons, 7 schematic SVGs, 4 placeholders and 4 overlay assets.')


if __name__=='__main__':build()
