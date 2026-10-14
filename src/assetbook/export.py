"""Dependency-free Excel and SVG export."""
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

from .domain import status
from .storage import FIELDS, HEADERS


def column_name(index):
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def xml_text(value):
    return escape("".join(c for c in str(value if value is not None else "") if ord(c) >= 32 or c in "\t\n\r"))


def export_xlsx(path, assets):
    rows = [["ID", *HEADERS, "状态", "续期次数", "数据来源", "原编号"]]
    for a in assets:
        rows.append([a["id"], *(a.get(key, "") for key in FIELDS), status(a), a["auto_renew_count"], a["source"], a.get("source_id", "")])
    sheets = []
    for i, row in enumerate(rows, 1):
        cells = []
        for j, value in enumerate(row, 1):
            ref = f"{column_name(j)}{i}"
            style = ' s="1"' if i == 1 else ""
            # Amount/rate/term/renew count are numeric. Accounts and all text remain literal strings.
            if i > 1 and j in (4, 8, 11, 15) and value not in (None, ""):
                cells.append(f'<c r="{ref}"{style}><v>{xml_text(value)}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{xml_text(value)}</t></is></c>')
        sheets.append(f'<row r="{i}">' + "".join(cells) + '</row>')
    end = f"{column_name(len(rows[0]))}{len(rows)}"
    sheet = '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="17" width="20" customWidth="1"/></cols><sheetData>' + "".join(sheets) + f'</sheetData><autoFilter ref="A1:{end}"/></worksheet>'
    files = {
        '[Content_Types].xml': '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
        '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml': '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="资产明细" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
        'xl/styles.xml': '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Microsoft YaHei"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Microsoft YaHei"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF245B50"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf fontId="0" fillId="0" borderId="0" xfId="0"/><xf fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>',
        'xl/worksheets/sheet1.xml': sheet,
    }
    with ZipFile(path, 'w', ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content.encode('utf-8'))


def export_svg(path, data, title, currency):
    width, height = 1000, max(360, len(data) * 48 + 130)
    highest = float(max((v for _, v in data), default=1)) or 1
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="#f7f8f5"/>',
             f'<text x="32" y="48" font-size="24" fill="#173c34" font-family="Microsoft YaHei,sans-serif">{escape(title)} · {escape(currency)}</text>']
    for index, (label, value) in enumerate(data):
        y = 96 + index * 48
        bar = float(value) / highest * 520
        parts.extend([f'<text x="32" y="{y+16}" font-size="14" font-family="Microsoft YaHei,sans-serif">{escape(label)}</text>',
                      f'<rect x="240" y="{y}" width="{bar:.1f}" height="22" rx="4" fill="#357565"/>',
                      f'<text x="{250+bar:.1f}" y="{y+16}" font-size="14" font-family="sans-serif">{value:,.2f}</text>'])
    parts.append('</svg>')
    Path(path).write_text("\n".join(parts), encoding="utf-8")
