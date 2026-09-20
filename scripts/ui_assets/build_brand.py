"""Reconstruct the supplied brand as editable paths; no generative model involved.

Requires Pillow, numpy and vtracer 0.6.15. Pass --source on first build.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image
import vtracer

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'assets/sentinel-ui'
NS = '{http://www.w3.org/2000/svg}'


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + '\n')


def trace(mask, directory, name):
    src, dst = directory / (name + '.png'), directory / (name + '.svg')
    Image.fromarray(np.where(mask, 0, 255).astype('uint8')).save(src)
    vtracer.convert_image_to_svg_py(str(src), str(dst), colormode='binary',
        mode='spline', filter_speckle=4, corner_threshold=15,
        length_threshold=0.5, max_iterations=20, splice_threshold=10,
        path_precision=3)
    nodes = []
    for node in ET.parse(dst).getroot():
        if node.tag == NS + 'path':
            node.tag = 'path'
            node.attrib.pop('fill', None)
            nodes.append(ET.tostring(node, encoding='unicode'))
    assert nodes, name
    return ''.join(nodes)


def svg(title, box, ink_paths, red_paths, ink, red):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{box}" role="img" aria-labelledby="title">'
        f'<title id="title">{title}</title><g fill="{ink}">{ink_paths}</g>'
        f'<g fill="{red}">{red_paths}</g></svg>')


def build(source=None):
    copied = OUT / 'brand/source/sentinel-logo-original.jpg'
    copied.parent.mkdir(parents=True, exist_ok=True)
    if source and Path(source).resolve() != copied.resolve():
        shutil.copyfile(source, copied)
    if not copied.is_file():
        raise SystemExit('First build requires --source with the supplied logo JPEG.')
    image = Image.open(copied).convert('RGB')
    a = np.asarray(image).astype('int16')
    red_mask = (a[:,:,0] > 80) & (a[:,:,0] > a[:,:,1]*1.8) & (a[:,:,0] > a[:,:,2]*1.6)
    ink_mask = (a.max(axis=2) < 150) & ~red_mask
    # Sampling only the solid interior avoids antialiased edge colors.
    red_rgb = Counter(image.crop((497,443,526,472)).getdata()).most_common(1)[0][0]
    ink_rgb = Counter(image.crop((506,233,518,260)).getdata()).most_common(1)[0][0]
    red = '#%02X%02X%02X' % red_rgb
    ink = '#%02X%02X%02X' % ink_rgb
    symbol_ink = ink_mask.copy()
    symbol_ink[720:,:] = False
    ys,xs = np.where(ink_mask | red_mask)
    full_box = f'{xs.min()-24} {ys.min()-24} {xs.max()-xs.min()+49} {ys.max()-ys.min()+49}'
    sy,sx = np.where(symbol_ink | red_mask)
    size = int(max(sx.max()-sx.min(),sy.max()-sy.min())+41)
    symbol_box = f'{(sx.min()+sx.max()-size)/2:g} {(sy.min()+sy.max()-size)/2:g} {size} {size}'
    with tempfile.TemporaryDirectory(prefix='sentinel-trace-') as temp:
        directory = Path(temp)
        paths = trace(ink_mask,directory,'ink')
        symbol_paths = trace(symbol_ink,directory,'symbol')
        red_paths = trace(red_mask,directory,'red')
    for variant,color in [('light',ink),('dark','#F3F6F8')]:
        write(OUT/f'brand/logo-{variant}.svg',svg('SENTINEL complete logo',full_box,paths,red_paths,color,red))
        write(OUT/f'brand/symbol-{variant}.svg',svg('SENTINEL',symbol_box,symbol_paths,red_paths,color,red))
        compact=(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" role="img" aria-labelledby="title">'
            f'<title id="title">SENTINEL compact symbol</title><g fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round">'
            '<circle cx="16" cy="16" r="13"/><path d="M3 16Q16 2 29 16Q16 30 3 16Z"/>'
            '<circle cx="16" cy="16" r="6"/><path d="M16 3v5M16 24v5"/></g>'
            f'<circle cx="16" cy="16" r="3" fill="{red}"/></svg>')
        write(OUT/f'brand/symbol-compact-{variant}.svg',compact)
    write(OUT/'brand/source-aligned-reconstruction.svg',svg('SENTINEL source-aligned reconstruction','0 0 1024 1024',paths,red_paths,ink,red))
    provenance={'source_filename':'SENTINEL LOGO.jpg','source_sha256':hashlib.sha256(copied.read_bytes()).hexdigest(),
        'source_dimensions':list(image.size),'method':'Two-color source-mask spline trace; lettering is outlined, not retyped.',
        'tracer':'vtracer 0.6.15','master_status':'Reconstructed candidate for user visual acceptance',
        'brand_center':red,'source_ink':ink,'sample_rect':[497,443,526,472],
        'intentional_differences':['JPEG noise and edge antialiasing removed','Solid ink/red colors normalized from interior samples',
            'Whitespace trimmed with clear space in usable masters','Compact symbol simplifies internal aperture/ring details'],
        'symbol_viewbox':symbol_box,'logo_viewbox':full_box}
    write(OUT/'brand/provenance.json',json.dumps(provenance,indent=2))
    tokens={'surface':{'canvas':'#101820','panel':'#1B2733','raised':'#243442'},
        'border':{'default':'#344653','control':'#8296A8'},
        'text':{'primary':'#F3F6F8','secondary':'#A9BAC8','on_accent':'#101820'},
        'accent':{'primary':'#47D7C8'},'status':{'warning':'#F2BA55','emergency':'#F06565'},
        'brand':{'center':red,'ink':ink},'focus':{'color':'#47D7C8','width':'2px','offset':'3px'},
        'space':{'1':'4px','2':'8px','3':'12px','4':'16px','5':'24px','6':'32px','7':'48px'},
        'radius':{'panel':'8px','pill':'999px'},'icon':{'size':'24px','stroke':'2px'},
        'font':{'body':'system-ui, sans-serif','mono':'ui-monospace, monospace'},
        'type':{'body':'16px','metadata':'14px','section':'24px','mission':'32px'}}
    write(OUT/'tokens/tokens.json',json.dumps(tokens,indent=2))
    declarations=[f'  --{group}-{name.replace("_","-")}: {value};' for group,values in tokens.items() for name,value in values.items()]
    write(OUT/'tokens/tokens.css','/* Generated from tokens.json. */\n:root {\n'+'\n'.join(declarations)+'\n}')
    print(json.dumps(provenance,indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source')
    build(parser.parse_args().source)
