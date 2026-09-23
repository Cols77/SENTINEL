"""Verify the deliverable kit, then write its manifest and validation report."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import re
import struct
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parents[2]
KIT=ROOT/'assets/sentinel-ui'
NS={'s':'http://www.w3.org/2000/svg'}
checks=[]


def require(condition,name):
    if not condition:raise AssertionError(name)
    checks.append(name)


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def contrast(a,b):
    def lum(color):
        rgb=[int(color[i:i+2],16)/255 for i in (1,3,5)]
        rgb=[x/12.92 if x<=.04045 else ((x+.055)/1.055)**2.4 for x in rgb]
        return sum(x*y for x,y in zip(rgb,[.2126,.7152,.0722]))
    x,y=sorted([lum(a),lum(b)]);return (y+.05)/(x+.05)


class Links(HTMLParser):
    def __init__(self):super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        for name,value in attrs:
            if name in ('src','href') and value:self.links.append(value)


def main():
    fixture=json.loads((KIT/'schematics/source-fixture.json').read_text())
    original=ROOT/'docs/superpowers/specs/contracts/mountain-v0.fixture.json'
    require(digest(original)==digest(KIT/'schematics/source-fixture.json'),'Source fixture unchanged')
    expected={s['section_id']:s['polygon'] for s in fixture['sections']}
    for path in (KIT/'schematics').rglob('*.svg'):
        tree=ET.parse(path);groups=tree.findall('.//s:g[@data-status]',NS)
        actual={g.attrib['id']:[[float(x) for x in pt.split(',')] for pt in g.find('s:polygon',NS).attrib['points'].split()] for g in groups}
        ids={'LEFT_ALLEY_MIDDLE','RIGHT_ALLEY_MIDDLE'} if path.name=='alley-route-only.svg' else set(expected)
        require(set(actual)==ids,'Zone membership '+path.name)
        require(all(actual[k]==expected[k] for k in actual),'Exact polygons '+path.name)
        require(all(g.attrib['data-status'] in fixture['compatibility_mapping'].values() for g in groups),'Persisted statuses '+path.name)
    for path in (KIT/'icons').glob('*.svg'):
        tree=ET.parse(path).getroot()
        require(tree.attrib.get('viewBox')=='0 0 24 24' and tree.attrib.get('stroke')=='currentColor' and tree.attrib.get('stroke-width')=='2','Icon format '+path.stem)
    require(len(list((KIT/'icons').glob('*.svg')))==14,'Fourteen separate interface icons')
    for path in KIT.rglob('*.svg'):
        tree=ET.parse(path).getroot()
        require(not tree.findall('.//s:image',NS) and not tree.findall('.//s:script',NS),'Vector-only safe structure '+str(path.relative_to(KIT)))
    for path in (KIT/'brand').glob('*.svg'):
        require(not ET.parse(path).findall('.//s:text',NS),'Brand lettering outlined '+path.name)
    prov=json.loads((KIT/'brand/provenance.json').read_text())
    require(prov['source_sha256']==digest(KIT/'brand/source/sentinel-logo-original.jpg'),'Original logo hash')
    for path in (KIT/'brand').rglob('*.png'):
        im=Image.open(path)
        require(im.mode=='RGBA' and im.getchannel('A').getextrema()==(0,255),'Transparency '+path.name)
        if path.name!='source-aligned-reconstruction.png':
            size=int(path.stem.rsplit('-',1)[1]);require(im.width==size,'Export width '+path.name)
            if not path.name.startswith('logo-'):require(im.height==size,'Square symbol '+path.name)
    for path in (KIT/'brand/favicon').glob('*.ico'):
        data=path.read_bytes();require(struct.unpack_from('<HHH',data)==(0,1,3),'ICO header '+path.name)
        require([data[6+i*16] for i in range(3)]==[16,32,48],'ICO sizes '+path.name)
    tokens=json.loads((KIT/'tokens/tokens.json').read_text());css=(KIT/'tokens/tokens.css').read_text()
    for group,vals in tokens.items():
        for name,value in vals.items():require(f'--{group}-{name.replace("_","-")}: {value};' in css,'Token agreement '+group+'.'+name)
    require(tokens['brand']['center']==prov['brand_center'],'Logo center color preserved')
    pairs={
        'body on panel':(tokens['text']['primary'],tokens['surface']['panel'],4.5),
        'metadata on panel':(tokens['text']['secondary'],tokens['surface']['panel'],4.5),
        'metadata on canvas':(tokens['text']['secondary'],tokens['surface']['canvas'],4.5),
        'teal action text':(tokens['text']['on_accent'],tokens['accent']['primary'],4.5),
        'warning action text':(tokens['text']['on_accent'],tokens['status']['warning'],4.5),
        'emergency action text':(tokens['text']['on_accent'],tokens['status']['emergency'],4.5),
        'focus on panel':(tokens['focus']['color'],tokens['surface']['panel'],3),
        'control border':(tokens['border']['control'],tokens['surface']['panel'],3),
        'blocked state label':(tokens['text']['secondary'],'#492E35',4.5),
        'hazard state label':(tokens['text']['secondary'],'#473B25',4.5)}
    ratios={}
    for name,(fg,bg,minimum) in pairs.items():
        ratio=contrast(fg,bg);ratios[name]=round(ratio,2);require(ratio>=minimum,'Contrast '+name)
    source=np.asarray(Image.open(KIT/'brand/source/sentinel-logo-original.jpg').convert('RGB')).astype('int16')
    red=(source[:,:,0]>80)&(source[:,:,0]>source[:,:,1]*1.8)&(source[:,:,0]>source[:,:,2]*1.6)
    source_mask=(source.max(2)<150)|red
    trace=np.asarray(Image.open(KIT/'brand/png/source-aligned-reconstruction.png').convert('RGBA'))[:,:,3]>127
    iou=float((source_mask&trace).sum()/(source_mask|trace).sum())
    require(iou>.94,'Source contour overlap exceeds 94%')
    metadata={'manifest.json','validation.json'}
    for path in KIT.rglob('*'):
        if not path.is_file() or path.name in metadata:continue
        if path.suffix in ('.html','.js','.css','.svg','.json','.md'):
            text=path.read_text()
            require(not re.search(r'(?:/Users/|/tmp/|Bearer\s+[A-Za-z0-9]{12}|(?:KIE_API_KEY|OPENAI_API_KEY)\s*=\s*\S+|sk-[A-Za-z0-9]{24})',text),'Portable/no secrets '+str(path.relative_to(KIT)))
            if path.suffix=='.html':
                parser=Links();parser.feed(text)
                for link in parser.links:
                    require(not link.startswith(('http:','https:','//')),'Offline link '+link)
                    target=link.split('#')[0]
                    if target and Path(target).name not in metadata:require((path.parent/target).exists(),'Local link '+link)
    components=(KIT/'component-specs.md').read_text()
    for check in ['WIFI_INTERFACE','DRONE_LINK','BATTERY_TELEMETRY','LIVE_VIDEO','LAYOUT_MARKERS','BOUNDED_AREA','HEIGHT_ENVELOPE','EMERGENCY_LATCH']:
        require(check in components,'Preflight coverage '+check)
    provenance={'brand':'User-supplied JPEG, deterministic reconstruction/exports; compact variants are original simplifications.',
        'icons':'Original SVG geometry created for SENTINEL.',
        'schematics':'Versioned WP0 fixture; exact original polygon coordinates.',
        'placeholders':'Original SVG placeholder artwork; no evidence content.',
        'overlays':'Original illustrative display treatments; no physical detection markers.',
        'tokens':'Approved design specification; logo center sampled from source.'}
    assets=[]
    for path in sorted(KIT.rglob('*')):
        if not path.is_file() or path.name in metadata:continue
        rel=path.relative_to(KIT).as_posix();category=rel.split('/')[0] if '/' in rel else 'documentation/review'
        entry={'id':rel.replace('/','.'),'path':rel,'format':path.suffix.lstrip('.'),'category':category,
            'purpose':path.stem.replace('-',' ').replace('_',' '),'provenance':provenance.get(category,'Local kit documentation or illustrative review; no coordinator integration.'),
            'variant':'dark-surface' if '-dark' in rel else 'light-surface' if '-light' in rel else 'default','bytes':path.stat().st_size,'sha256':digest(path)}
        if path.suffix in ('.png','.jpg'):
            with Image.open(path) as im:entry.update(width=im.width,height=im.height)
        assets.append(entry)
    require(len({asset['id'] for asset in assets}) == len(assets), 'Unique asset IDs across formats')
    (KIT/'manifest.json').write_text(json.dumps({'kit':'SENTINEL shared-screen interface kit','version':'0.1.0','assets':assets,'excluded_metadata':sorted(metadata),'source_logo_sha256':prov['source_sha256']},indent=2)+'\n')
    existing=json.loads((KIT/'validation.json').read_text()) if (KIT/'validation.json').exists() else {}
    result={'status':'passed','asset_count':len(assets),'checks_passed':len(checks),'checks':checks,'contrast_ratios':ratios,
        'source_mask_iou':round(iou,5),'brand_review':'Author inspected source-aligned comparison; user acceptance of reconstructed candidate remains open.',
        'scope':'Asset invariants only; not WP2 runtime conformance','browser':existing.get('browser',{'status':'not yet run'})}
    (KIT/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'assets':len(assets),'checks_passed':len(checks),'source_mask_iou':round(iou,5),'contrast':ratios},indent=2))


if __name__=='__main__':main()
