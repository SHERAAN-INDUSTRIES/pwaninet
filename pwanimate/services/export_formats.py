"""Additional server-side export formats. No network services or shared office profile."""
import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from django.conf import settings
from django.utils import timezone

FORMATS = {
    'pdf': 'application/pdf',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'doc': 'application/msword',
    'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
}


def stamp():
    return 'spark.vasto · ' + timezone.localtime().strftime('%d %B %Y')


def render_doc(docx_bytes):
    executable = shutil.which('libreoffice') or shutil.which('soffice')
    if not executable:
        raise RuntimeError('Legacy DOC conversion requires LibreOffice. Choose DOCX meanwhile.')
    with tempfile.TemporaryDirectory(prefix='spark-doc-') as directory:
        root = Path(directory)
        (root / 'source.docx').write_bytes(docx_bytes)
        timeout = max(10, min(int(getattr(settings, 'SPARK_OFFICE_EXPORT_TIMEOUT', 60)), 180))
        try:
            result = subprocess.run([executable, '-env:UserInstallation=' + (root / 'profile').as_uri(),
                '--headless', '--convert-to', 'doc:MS Word 97', '--outdir', str(root),
                str(root / 'source.docx')], capture_output=True, timeout=timeout, check=False,
                env={**os.environ, 'GSETTINGS_BACKEND': 'memory', 'SAL_USE_VCLPLUGIN': 'svp'})
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('Legacy Word conversion timed out. Retry or choose DOCX.') from exc
        target = root / 'source.doc'
        if result.returncode or not target.is_file():
            raise RuntimeError('Legacy Word conversion failed. Retry or choose DOCX.')
        data = target.read_bytes()
        if not data.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):
            raise RuntimeError('The converter did not produce a genuine legacy Word document.')
        return data


def render_pptx(title, message):
    # Application runtime dependency already used by this Django project.
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from pwanimate.services.generated_documents import _content_blocks, _plain_inline, _message_math_expressions, _render_math_images
    presentation = Presentation()
    presentation.slide_width, presentation.slide_height = Inches(13.333), Inches(7.5)
    presentation.core_properties.title = title
    presentation.core_properties.author = 'spark.vasto'
    marker = stamp()
    math_images = _render_math_images(_message_math_expressions(title, message))

    def add_slide(heading, lines, code=False):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        title_box = slide.shapes.add_textbox(Inches(.7), Inches(.45), Inches(11.9), Inches(1.2))
        frame = title_box.text_frame
        frame.word_wrap = True
        frame.text = heading
        for p in frame.paragraphs:
            p.font.name, p.font.size, p.font.bold = 'Liberation Sans', Pt(20 if len(heading) > 150 else 24 if len(heading) > 90 else 30), True
            p.font.color.rgb = RGBColor.from_string('17324D')
        body = slide.shapes.add_textbox(Inches(.8), Inches(1.65), Inches(11.7), Inches(4.95)).text_frame
        body.word_wrap = True
        for index, line in enumerate(lines):
            p = body.paragraphs[0] if index == 0 else body.add_paragraph()
            p.text = line
            p.font.name, p.font.size = ('Liberation Mono', Pt(17)) if code else ('Liberation Serif', Pt(22))
            p.font.color.rgb = RGBColor.from_string('263648')
            p.space_after = Pt(12)
        foot = slide.shapes.add_textbox(Inches(.75), Inches(7.02), Inches(11.8), Inches(.25)).text_frame
        foot.text = f'{marker}                                      {len(presentation.slides)}'
        foot.paragraphs[0].font.name, foot.paragraphs[0].font.size = 'Liberation Sans', Pt(10)
        foot.paragraphs[0].font.color.rgb = RGBColor.from_string('64748B')
        return slide

    # Pagination is based on bounded lines; never silently discard overflow.
    import textwrap
    heading, pending, pending_code = title, [], False
    add_slide(title, ['Revision presentation', marker])
    def flush():
        nonlocal pending
        if pending:
            add_slide(heading, pending, pending_code)
            pending = []
    for block in _content_blocks(message.content):
        kind = block['type']
        if kind == 'heading':
            flush()
            heading = _plain_inline(block['text'])[:180]
            continue
        if kind == 'math' and block['text'] in math_images:
            flush()
            slide = add_slide(heading, [])
            data, width, height, _ = math_images[block['text']]
            w = min(10, width / 110)
            h = w * height / max(1, width)
            if h > 3.8: w *= 3.8 / h; h = 3.8
            slide.shapes.add_picture(io.BytesIO(data), Inches(.9), Inches(2.1), width=Inches(w), height=Inches(h))
            slide.notes_slide.notes_text_frame.text = 'Equation source: ' + block['text']
            continue
        if kind == 'table':
            flush()
            headers = block.get('headers', [])
            rows = block.get('rows', [])
            if not headers: continue
            # Wide/verbose tables cannot fit legibly in a fixed-height slide.
            # Preserve their complete cells as paginated text instead.
            if len(headers) > 5 or any(len(_plain_inline(str(cell))) > 80 for row in [headers, *rows] for cell in row):
                pending_code = False
                for row in rows or [[]]:
                    for index, cell in enumerate(row):
                        label = headers[index] if index < len(headers) else f'Column {index + 1}'
                        for line in textwrap.wrap(_plain_inline(f'{label}: {cell}'), width=80) or ['']:
                            if len(pending) >= 7: flush()
                            pending.append(line)
                flush()
                continue
            for first in range(0, max(1, len(rows)), 7):
                slide = add_slide(heading, [])
                group = rows[first:first + 7]
                table = slide.shapes.add_table(len(group) + 1, len(headers), Inches(.75), Inches(1.75), Inches(11.8), Inches(min(4.7, .52 * (len(group) + 1)))).table
                for row_index, values in enumerate([headers, *group]):
                    for col, text in enumerate(values[:len(headers)]):
                        cell = table.cell(row_index, col)
                        cell.text = _plain_inline(text)
                        cell.fill.solid()
                        cell.fill.fore_color.rgb = RGBColor.from_string('17324D' if row_index == 0 else 'F1F5F9' if row_index % 2 else 'FFFFFF')
                        for p in cell.text_frame.paragraphs:
                            p.font.name, p.font.size = 'Liberation Sans', Pt(16)
                            p.font.color.rgb = RGBColor.from_string('FFFFFF' if row_index == 0 else '263648')
            continue
        code = kind == 'code'
        if pending and code != pending_code:
            flush()
        pending_code = code
        text = block.get('text', '')
        # Equations retain their source notation in editable decks. No image of
        # a formula is mislabeled as an editable native equation.
        text = text if code or kind == 'math' else _plain_inline(text)
        for paragraph in text.splitlines():
            for line in textwrap.wrap(paragraph, width=78 if code else 80, replace_whitespace=False) or ['']:
                if len(pending) >= (10 if code else 7):
                    flush()
                pending.append(line)
    flush()
    if message.sources:
        heading, pending_code = 'Sources', False
        pending = []
        for source in message.sources:
            label = ' · '.join(str(source[key]) for key in ('citation', 'title', 'url') if source.get(key)) or 'Source'
            for line in textwrap.wrap(str(label), 80):
                if len(pending) >= 7: flush()
                pending.append(line)
        flush()
    max_slides = max(5, min(int(getattr(settings, 'SPARK_EXPORT_MAX_SLIDES', 250)), 500))
    if len(presentation.slides) > max_slides:
        raise ValueError(f'This content needs more than {max_slides} slides. Choose a shorter scope.')
    stream = io.BytesIO()
    presentation.save(stream)
    return stream.getvalue()
