"""Bounded extraction of actual tabular content, without executing page scripts."""
import csv
import io


MAX_ROWS = 1000
MAX_COLUMNS = 32
MAX_TABLE_CHARS = 50000


def csv_tables(text: str, *, truncated=False) -> list[dict]:
    try:
        reader = csv.reader(io.StringIO(text))
        columns = next(reader)
        rows = []
        remaining = MAX_TABLE_CHARS
        for row in reader:
            if len(rows) >= MAX_ROWS or sum(map(len, row)) > remaining:
                truncated = True
                break
            if len(row) == len(columns):
                remaining -= sum(map(len, row))
                truncated = truncated or any(len(cell) > 500 for cell in row)
                rows.append([cell[:500] for cell in row[:MAX_COLUMNS]])
        if not rows or len(columns) > MAX_COLUMNS:
            return []
        return [{"columns": columns, "rows": rows, "truncated": truncated,
                 "extraction_method": "csv", "source_bound": True}]
    except (csv.Error, StopIteration):
        return []


def html_tables(html: str) -> list[dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    output = []
    remaining = MAX_TABLE_CHARS
    for table in soup.find_all("table", limit=5):
        rows = table.find_all("tr", limit=MAX_ROWS + 2)
        if len(rows) < 2:
            continue
        cells = []
        truncated = len(rows) > MAX_ROWS + 1
        for row in rows:
            values = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"], recursive=False)]
            if sum(map(len, values)) > remaining:
                truncated = True
                break
            remaining -= sum(map(len, values))
            truncated = truncated or any(len(cell) > 500 for cell in values)
            cells.append([cell[:500] for cell in values])
        if not cells:
            continue
        columns = cells[0]
        if not columns or len(columns) > MAX_COLUMNS:
            continue
        output.append({"columns": columns, "rows": [row for row in cells[1:MAX_ROWS + 1] if len(row) == len(columns)],
                       "caption": table.caption.get_text(" ", strip=True)[:500] if table.caption else "",
                       "truncated": truncated, "extraction_method": "html_table", "source_bound": True})
    return output
