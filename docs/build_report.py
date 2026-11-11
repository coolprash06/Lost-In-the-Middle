"""Build docs/Lost_in_the_Middle_Report.docx (and .pdf) using only the Python standard library.

A .docx is a zip of XML files; this writes them directly (no python-docx / lxml,
which Windows Smart App Control may block). The PDF is exported by Microsoft Word
through PowerShell, if Word is installed.

Usage (from the project folder):
    python docs/build_report.py           # .docx + .pdf
    python docs/build_report.py --no-pdf  # .docx only
"""
import struct
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

DOCS = Path(__file__).resolve().parent
IMAGES = DOCS / "images"
OUT = DOCS / "Lost_in_the_Middle_Report.docx"
PDF_OUT = DOCS / "Lost_in_the_Middle_Report.pdf"

REPO_URL = "https://github.com/coolprash06/Lost-In-the-Middle"

# (image file in docs/images/, caption)
FIGURES = [
    ("figure1_run9_chart.png",
     "Figure 1: Run 9 — qwen/qwen3.8-27b, hard mode, N = 100. Accuracy is 100% at every position."),
    ("figure2_run9_table.png",
     "Figure 2: Run 9 results table — 0 answers fooled by look-alike keys."),
]
FIGURE_WIDTH = 0.82  # fraction of the text width (so both figures fit on one page)

# A4, 1-inch margins (sizes in DXA: 1440 = 1 inch)
PAGE_W, PAGE_H, MARGIN = 11906, 16838, 1440
CONTENT_W = PAGE_W - 2 * MARGIN  # 9026
EMU_PER_DXA = 635

FONT = "Calibri"
DARK = "1F3864"
ACCENT = "2E5597"

# ---------------------------------------------------------------- XML helpers

def run(text, bold=False, italic=False, color=None, size=None):
    props = ""
    if bold:
        props += "<w:b/>"
    if italic:
        props += "<w:i/>"
    if color:
        props += f'<w:color w:val="{color}"/>'
    if size:
        props += f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def para(runs, style=None, num_id=None, extra_ppr="", run_size=None):
    """runs: str or list of (text, bold) tuples / strings."""
    if isinstance(runs, str):
        runs = [runs]
    body = ""
    for r in runs:
        if isinstance(r, tuple):
            body += run(r[0], bold=r[1], size=run_size)
        else:
            body += run(r, size=run_size)
    ppr = ""
    if style:
        ppr += f'<w:pStyle w:val="{style}"/>'
    if num_id:
        ppr += f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{num_id}"/></w:numPr>'
    ppr += extra_ppr
    return f"<w:p><w:pPr>{ppr}</w:pPr>{body}</w:p>"


def heading(text, level):
    return para(text, style=f"Heading{level}")


def bullet(runs):
    return para(runs, style="ListParagraph", num_id=1)


def numbered(text):
    return para(text, style="ListParagraph", num_id=2)


def table(headers, rows, widths, font_size=19):
    assert sum(widths) == CONTENT_W, sum(widths)
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)

    def cell(text, w, header=False, shade=None):
        shd = f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>' if shade else ""
        txt = run(text, bold=header, color="FFFFFF" if header else None, size=font_size)
        return (f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>{shd}</w:tcPr>'
                f'<w:p><w:pPr><w:spacing w:before="0" w:after="0" w:line="252" w:lineRule="auto"/>'
                f'<w:keepLines/></w:pPr>{txt}</w:p></w:tc>')

    head = ('<w:tr><w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>'
            + "".join(cell(h, w, header=True, shade=DARK) for h, w in zip(headers, widths))
            + "</w:tr>")
    body = ""
    for i, r in enumerate(rows):
        shade = "F2F5FA" if i % 2 else None
        body += ('<w:tr><w:trPr><w:cantSplit/></w:trPr>'
                 + "".join(cell(c, w, shade=shade) for c, w in zip(r, widths)) + "</w:tr>")
    border = 'w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"'
    tblpr = (f'<w:tblPr><w:tblW w:w="{CONTENT_W}" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
             f'<w:tblBorders><w:top {border}/><w:left {border}/><w:bottom {border}/>'
             f'<w:right {border}/><w:insideH {border}/><w:insideV {border}/></w:tblBorders>'
             '<w:tblCellMar><w:top w:w="60" w:type="dxa"/><w:left w:w="100" w:type="dxa"/>'
             '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar>'
             '</w:tblPr>')
    # Empty paragraph after a table keeps spacing before the next block.
    return f"<w:tbl>{tblpr}<w:tblGrid>{grid}</w:tblGrid>{head}{body}</w:tbl>" + para("", extra_ppr='<w:spacing w:after="60"/>')


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"not a PNG: {path}"
    return struct.unpack(">II", head[16:24])


def figure(n, rel_id, image_path, caption, keep_with_next=False):
    """keep_with_next=True keeps this figure on the same page as the next one."""
    w_px, h_px = png_size(image_path)
    cx = round(CONTENT_W * EMU_PER_DXA * FIGURE_WIDTH)
    cy = round(cx * h_px / w_px)
    drawing = (
        f'<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{n}" name="Figure {n}" descr="{escape(caption)}"/>'
        '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:pic><pic:nvPicPr><pic:cNvPr id="{n}" name="{image_path.name}"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rel_id}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        '<a:ln w="6350"><a:solidFill><a:srgbClr val="BFBFBF"/></a:solidFill></a:ln></pic:spPr>'
        '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
    )
    img_p = (f'<w:p><w:pPr><w:keepNext/><w:spacing w:before="120" w:after="60"/><w:jc w:val="center"/></w:pPr>'
             f'{drawing}</w:p>')
    return img_p + para(caption, style="Caption", extra_ppr="<w:keepNext/>" if keep_with_next else "")


# ---------------------------------------------------------------- content

def build_body():
    b = []
    b.append(para("Lost in the Middle — Test Runs and Limitations", style="Title"))
    b.append(para("11 November 2026", style="Subtitle"))
    link = (f'<w:hyperlink r:id="rIdRepo" w:history="1"><w:r><w:rPr><w:rStyle w:val="Hyperlink"/></w:rPr>'
            f'<w:t xml:space="preserve">{escape(REPO_URL)}</w:t></w:r></w:hyperlink>')
    b.append(f'<w:p><w:pPr><w:pStyle w:val="RepoLine"/></w:pPr>{run("Code repository: ")}{link}</w:p>')

    b.append(heading("Overview", 1))
    b.append(para('We built a Streamlit app that re-runs the key-value retrieval experiment from Liu et al., '
                  '"Lost in the Middle: How Language Models Use Long Contexts" (TACL 2024), on current models '
                  'through the Groq API. Across every model and setting we could fit within the free tier, we did '
                  'not reproduce the U-shaped curve.'))
    b.append(para("How one test works:", extra_ppr='<w:keepNext/>'))
    for t in [
        "The app builds a JSON object of N key-value pairs, where every key and value is a random UUID. "
        "Because they are random, the model cannot know them from training and must find the answer in the context.",
        "One pair is the target. It is placed at one of five positions: First, 25%, Middle, 75% or Last.",
        'The model is asked: "What is the value for key <target key>? Answer with only the value." Temperature is 0.',
        "The answer counts as correct if the target's value appears in the reply.",
        "This repeats T times per position, and the app plots accuracy by position. "
        "Every run is saved as a JSON file in the results/ folder.",
    ]:
        b.append(numbered(t))
    b.append(para("The paper's finding is that accuracy is highest at the start and end of the context and lowest "
                  "in the middle, forming a U-shape. Our app tests whether today's models still show that pattern."))

    b.append(heading("Test runs", 1))
    b.append(para("We ran every test on 11 November 2026 with T = 1 trial per position. Five runs scored 100% at "
                  "every position, one scored 0% at every position, and three attempts failed with an error before "
                  "producing a result."))
    b.append(table(
        ["#", "Model", "N (pairs)", "Settings", "Result", "Notes"],
        [
            ["1", "llama-3.1-8b-instant", "75", "Default", "Error 404", "Model no longer offered by Groq"],
            ["2", "openai/gpt-oss-20b", "75", "Reasoning effort low", "100% at all 5 positions", "About 4,700 tokens per request"],
            ["3", "openai/gpt-oss-20b", "200", "Reasoning effort low", "Error 413", "Request was 10,506 tokens; limit is 8,000 per minute"],
            ["4", "openai/gpt-oss-20b", "140", "Reasoning effort low", "Error 413", "Request was 8,092 tokens; just over the limit"],
            ["5", "openai/gpt-oss-20b", "125", "Reasoning effort low", "100% at all 5 positions", "Largest context that fit, about 7,300 tokens"],
            ["6", "openai/gpt-oss-20b", "50", "Reasoning effort low", "100% at all 5 positions", "Run by mistake before switching model"],
            ["7", "allam-2-7b", "50", "Non-reasoning", "0% at all 5 positions", "Could not copy a UUID correctly; one reply was 63 zeros"],
            ["8", "qwen/qwen3.8-27b", "100", "Thinking off", "100% at all 5 positions", "Closest match to the paper's setup"],
            ["9", "qwen/qwen3.8-27b", "100", "Thinking off, hard mode, 10 look-alike keys", "100% at all 5 positions", "0 answers fooled by a look-alike key"],
        ],
        [450, 1850, 800, 1800, 1700, 2426],
    ))
    b.append(para("In hard mode, 10 distractor keys are copies of the target key with 2 or 3 characters changed. "
                  "They are spread randomly across the list, so the model has to read the exact key carefully at "
                  "every position."))
    for i, (name, caption) in enumerate(FIGURES, start=1):
        # Keep each figure with the next one so they always share a page.
        b.append(figure(i, f"rIdImg{i}", IMAGES / name, caption, keep_with_next=i < len(FIGURES)))

    b.append(heading("What the results show", 1))
    b.append(para("No model showed the U-shape: every model scored either 100% or 0% at all five positions, so "
                  "position made no difference."))
    b.append(bullet([("Mid-size and large models no longer get lost at these lengths.", True),
                     " gpt-oss-20b (a reasoning model) and Qwen 27B (with thinking turned off) found the target at "
                     "every position, up to about 7,300 tokens of context. Qwen also ignored all 10 look-alike keys "
                     "in hard mode."]))
    b.append(bullet([("A very small model fails everywhere, not just in the middle.", True),
                     " allam-2-7b could not reliably copy a 36-character UUID. It dropped characters, copied JSON "
                     "symbols into its answer, and once got stuck repeating zeros. Its failures are about copying, "
                     "not position."]))
    b.append(bullet([("The useful range is narrow.", True),
                     " The U-curve appears in models that can do the task but struggle with long input. Within our "
                     "token budget, we found no model on Groq's free tier in that range."]))
    b.append(para("One likely reason is that newer models are trained on much longer contexts than the 2023 models "
                  "in the paper. At our context sizes (2,800 to 7,300 tokens), this task appears to be too easy for them."))

    b.append(heading("Limitations", 1))
    b.append(para("The biggest limit is Groq's free tier: 8,000 tokens per minute, which caps one request at about "
                  "7,300 tokens of context. The paper tested longer contexts on older models, so our results cover "
                  "only the short end of its range."))
    b.append(heading("Token limits", 2))
    b.append(table(
        ["Limit", "Value", "Effect on our tests"],
        [
            ["Tokens per minute (gpt-oss-20b, free tier)", "8,000",
             "A single request must fit under it, or Groq rejects it with error 413. Waiting does not help."],
            ["What counts toward the limit", "Prompt tokens + the reserved reply length",
             "gpt-oss reserves 1,024 tokens for its reasoning and answer, leaving less room for the context."],
            ["Largest N that fit (gpt-oss-20b)", "125 pairs", "N = 140 was 8,092 tokens, just over the limit."],
            ["allam-2-7b context window", "About 4,096 tokens",
             "Limits it to about 70 pairs, regardless of the per-minute limit."],
            ["Calls per minute", "About 1",
             "Each request uses most of the minute's budget, so the app waits about 60 seconds between calls. "
             "One 5-call run takes 2 to 5 minutes."],
        ],
        [2700, 1700, 4626],
    ))
    b.append(heading("Why UUIDs are expensive", 2))
    b.append(para("Random UUIDs split into many small tokens. We measured about 1.7 characters per token, compared "
                  "with roughly 4 for normal English. One key-value pair costs about 50 tokens, so 100 pairs is "
                  "already about 5,400 tokens."))
    b.append(heading("How the app handles the limits", 2))
    b.append(bullet("It estimates each request's tokens before running, with a 10% safety margin, and refuses to "
                    "start if one request would exceed the limit. It also shows the largest N that fits."))
    b.append(bullet("It tracks tokens used in the last 60 seconds and waits with a live countdown before each call."))
    b.append(bullet("It shows Groq's 413 and context-length errors directly instead of retrying them."))
    b.append(heading("Other limitations", 2))
    b.append(bullet([("One trial per position.", True),
                     " With T = 1, each result is a single answer. A 100% or 0% result is clear, but small "
                     "differences between positions would need more trials, which the rate limit makes slow "
                     "(T = 3 at N = 75 takes about 15 minutes)."]))
    b.append(bullet([("Few models available.", True),
                     " Groq's free tier offered only three usable text models. The model named in our first plan, "
                     "llama-3.1-8b-instant, had been removed."]))
    b.append(bullet([("Our hard mode is our own addition.", True),
                     " Look-alike keys are not part of the paper's experiment. They make the task harder but are "
                     "not a direct comparison."]))
    b.append(bullet([("Windows setup.", True),
                     " Smart App Control on the test laptop blocked the compiled files of new pandas and pyarrow "
                     "versions, so the app was rebuilt to run without them. This did not affect the results."]))

    b.append(heading("Conclusion", 1))
    b.append(para("Within about 7,300 tokens of context, current models on Groq did not get lost in the middle of "
                  "the paper's key-value task, even with look-alike distractors. The only failing model failed at "
                  "every position, for a different reason. Testing whether the U-curve returns at longer contexts "
                  "would need a paid tier or another provider with a higher token limit, plus more trials per position."))
    return "".join(b)


# ---------------------------------------------------------------- package parts

NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
      'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')
XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def document_xml():
    sect = (f'<w:sectPr><w:footerReference w:type="default" r:id="rIdFooter"/>'
            f'<w:pgSz w:w="{PAGE_W}" w:h="{PAGE_H}"/>'
            f'<w:pgMar w:top="{MARGIN}" w:right="{MARGIN}" w:bottom="{MARGIN}" w:left="{MARGIN}" '
            'w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>')
    return f"{XML_HEAD}<w:document {NS}><w:body>{build_body()}{sect}</w:body></w:document>"


def styles_xml():
    def pstyle(sid, name, ppr="", rpr="", based="Normal", nxt="Normal"):
        return (f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/>'
                f'<w:basedOn w:val="{based}"/><w:next w:val="{nxt}"/><w:qFormat/>'
                f'<w:pPr>{ppr}</w:pPr><w:rPr>{rpr}</w:rPr></w:style>')
    font = f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:eastAsia="{FONT}" w:cs="{FONT}"/>'
    return (
        f'{XML_HEAD}<w:styles {NS}>'
        f'<w:docDefaults><w:rPrDefault><w:rPr>{font}<w:sz w:val="22"/><w:szCs w:val="22"/>'
        '<w:lang w:val="en-GB"/></w:rPr></w:rPrDefault>'
        '<w:pPrDefault><w:pPr><w:spacing w:after="140" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
        '</w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/>'
        f'<w:rPr>{font}<w:color w:val="222222"/></w:rPr></w:style>'
        + pstyle("Title", "Title", '<w:spacing w:before="0" w:after="60"/><w:keepNext/>',
                 f'{font}<w:b/><w:color w:val="{DARK}"/><w:sz w:val="40"/><w:szCs w:val="40"/>', nxt="Subtitle")
        + pstyle("Subtitle", "Subtitle", '<w:spacing w:before="0" w:after="60"/><w:keepNext/>',
                 f'{font}<w:color w:val="595959"/><w:sz w:val="24"/><w:szCs w:val="24"/>')
        # Repository line under the date; carries the rule that ends the title block.
        + pstyle("RepoLine", "Repository Line",
                 '<w:pBdr><w:bottom w:val="single" w:sz="8" w:space="8" w:color="' + ACCENT + '"/></w:pBdr>'
                 '<w:spacing w:before="0" w:after="320"/>',
                 f'{font}<w:color w:val="595959"/><w:sz w:val="21"/><w:szCs w:val="21"/>')
        + ('<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/>'
           '<w:uiPriority w:val="99"/><w:unhideWhenUsed/>'
           '<w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style>')
        + pstyle("Heading1", "heading 1",
                 '<w:keepNext/><w:keepLines/><w:spacing w:before="360" w:after="120"/><w:outlineLvl w:val="0"/>',
                 f'{font}<w:b/><w:color w:val="{DARK}"/><w:sz w:val="32"/><w:szCs w:val="32"/>')
        + pstyle("Heading2", "heading 2",
                 '<w:keepNext/><w:keepLines/><w:spacing w:before="240" w:after="80"/><w:outlineLvl w:val="1"/>',
                 f'{font}<w:b/><w:color w:val="{ACCENT}"/><w:sz w:val="26"/><w:szCs w:val="26"/>')
        + pstyle("ListParagraph", "List Paragraph",
                 '<w:spacing w:after="80"/><w:ind w:left="720" w:hanging="360"/><w:contextualSpacing/>')
        + pstyle("Caption", "caption", '<w:jc w:val="center"/><w:spacing w:before="0" w:after="240"/>',
                 f'{font}<w:i/><w:color w:val="404040"/><w:sz w:val="19"/><w:szCs w:val="19"/>')
        + pstyle("Footer", "footer", '<w:jc w:val="center"/><w:spacing w:after="0"/>',
                 f'{font}<w:color w:val="808080"/><w:sz w:val="18"/><w:szCs w:val="18"/>')
        + '</w:styles>'
    )


def numbering_xml():
    def abstract(aid, fmt, text, font=None):
        rpr = f'<w:rPr><w:rFonts w:ascii="{font}" w:hAnsi="{font}"/></w:rPr>' if font else ""
        return (f'<w:abstractNum w:abstractNumId="{aid}"><w:multiLevelType w:val="singleLevel"/>'
                f'<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/>'
                f'<w:lvlText w:val="{text}"/><w:lvlJc w:val="left"/>'
                f'<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr>{rpr}</w:lvl></w:abstractNum>')
    return (f'{XML_HEAD}<w:numbering {NS}>'
            + abstract(0, "bullet", "\u2022", FONT) + abstract(1, "decimal", "%1.")
            + '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
            + '<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num></w:numbering>')


def footer_xml():
    return (f'{XML_HEAD}<w:ftr {NS}><w:p><w:pPr><w:pStyle w:val="Footer"/></w:pPr>'
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p></w:ftr>')


CONTENT_TYPES = (
    f'{XML_HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Default Extension="png" ContentType="image/png"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
    '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    '</Types>')

ROOT_RELS = (
    f'{XML_HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    '</Relationships>')

# Title only; deliberately no creator / lastModifiedBy (no names in the file).
CORE = (
    f'{XML_HEAD}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    '<dc:title>Lost in the Middle — Test Runs and Limitations</dc:title>'
    '<dcterms:created xsi:type="dcterms:W3CDTF">2026-11-11T00:00:00Z</dcterms:created>'
    '</cp:coreProperties>')


def document_rels():
    rels = ('<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '<Relationship Id="rIdNumbering" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
            '<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
            '<Relationship Id="rIdRepo" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            f'Target="{escape(REPO_URL)}" TargetMode="External"/>')
    for i, (name, _) in enumerate(FIGURES, start=1):
        rels += (f'<Relationship Id="rIdImg{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                 f'Target="media/{name}"/>')
    return f'{XML_HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>'


def export_pdf():
    """Have Microsoft Word open the .docx read-only and export it as PDF."""
    ps = (
        "$w = New-Object -ComObject Word.Application; $w.Visible = $false; $w.DisplayAlerts = 0; "
        f"try {{ $d = $w.Documents.Open('{OUT}', $false, $true, $false); "
        # ExportAsFixedFormat(Output, 17=PDF, OpenAfter, OptimizeFor=print, Range=all, From, To,
        #   Item=content, IncludeDocProps, KeepIRM, CreateBookmarks=headings, DocStructureTags,
        #   BitmapMissingFonts, PDF/A)
        f"$d.ExportAsFixedFormat('{PDF_OUT}', 17, $false, 0, 0, 1, 1, 0, $true, $true, 1, $true, $true, $false); "
        "$d.Close(0) } finally { $w.Quit() }"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    print("wrote", PDF_OUT)


def main():
    for name, _ in FIGURES:
        if not (IMAGES / name).exists():
            sys.exit(f"Missing image: {IMAGES / name}")

    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", ROOT_RELS)
        z.writestr("docProps/core.xml", CORE)
        z.writestr("word/document.xml", document_xml())
        z.writestr("word/styles.xml", styles_xml())
        z.writestr("word/numbering.xml", numbering_xml())
        z.writestr("word/footer1.xml", footer_xml())
        z.writestr("word/_rels/document.xml.rels", document_rels())
        for name, _ in FIGURES:
            z.write(IMAGES / name, f"word/media/{name}")
    print("wrote", OUT)

    if "--no-pdf" not in sys.argv:
        export_pdf()


if __name__ == "__main__":
    main()
