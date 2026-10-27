"""Decode remote files using the same image/PDF libraries used by the app."""
import base64
import hashlib
import io
import json
from urllib.parse import quote

import fitz
from PIL import Image

from supabase_probe import ROOT
from verify_supabase_storage import BUCKET, request


def main():
    output = ROOT / 'backend/storage/supabase-proof'
    proof = json.loads((output / 'latest.json').read_text())
    report = {'run_id': proof['run_id'], 'pdfs': [], 'images': []}
    for entry in proof['files']:
        key = entry['object_key']
        if not key.endswith(('.pdf', '.png')):
            continue
        _, data = request('GET', '/storage/v1/object/' + BUCKET + '/' + quote(key, safe='/'))
        assert hashlib.sha256(data).hexdigest() == entry['sha256']
        local = ROOT / 'backend/storage' / entry['local_relative_path']
        if key.endswith('.pdf'):
            with fitz.open(stream=data, filetype='pdf') as pdf, fitz.open(local) as source:
                assert len(pdf) == len(source)
                for n in range(len(pdf)):
                    assert pdf[n].get_pixmap(dpi=72).samples == source[n].get_pixmap(dpi=72).samples
                preview = output / (local.parent.name + '-downloaded-pdf-page1.png')
                pdf[0].get_pixmap(dpi=110).save(preview)
                report['pdfs'].append({'object_key': key, 'pages': len(pdf), 'all_pages_rendered': True,
                                      'rendered_pixels_match_local': True, 'preview': str(preview)})
        else:
            with Image.open(io.BytesIO(data)) as image, Image.open(local) as source:
                image.load()
                assert image.convert('RGB').tobytes() == source.convert('RGB').tobytes()
                # The extraction client embeds PNG bytes in a base64 data URL.
                payload = 'data:image/png;base64,' + base64.b64encode(data).decode()
                roundtrip = base64.b64decode(payload.split(',', 1)[1], validate=True)
                with Image.open(io.BytesIO(roundtrip)) as decoded:
                    decoded.load()
                    assert decoded.convert('RGB').tobytes() == image.convert('RGB').tobytes()
                report['images'].append({'object_key': key, 'width': image.width, 'height': image.height,
                                         'decoded_pixels_match_local': True, 'vision_payload_roundtrip': True})
                if '/charts/' in key:
                    image.save(output / 'downloaded-chart-preview.png')
        print('PASS decode ' + entry['local_relative_path'], flush=True)
    report['pdf_count'] = len(report['pdfs'])
    report['image_count'] = len(report['images'])
    report['actual_model_call_made'] = False
    (output / 'decoding-proof.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'pdfs': report['pdf_count'], 'images': report['image_count'], 'all_decoding_checks': 'passed'}))


if __name__ == '__main__':
    main()
