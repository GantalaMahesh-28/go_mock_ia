import os
import uuid
import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

def generate_excel(records: list, question: str, sql: str, export_dir: str = "./data/excel_exports") -> str:
    """
    Generates a beautifully styled, multi-sheet Excel report with a BCG theme.
    Returns the filename of the generated file.
    """
    # Create export directory if it doesn't exist
    os.makedirs(export_dir, exist_ok=True)
    
    # Generate unique filename
    file_id = str(uuid.uuid4())
    filename = f"ul_report_{file_id[:8]}.xlsx"
    filepath = os.path.join(export_dir, filename)
    
    wb = Workbook()
    
    # Styles
    font_family = "Segoe UI"
    
    # Title Styles
    title_font = Font(name=font_family, size=16, bold=True, color="00A651")
    section_font = Font(name=font_family, size=12, bold=True, color="333333")
    key_font = Font(name=font_family, size=10, bold=True, color="555555")
    value_font = Font(name=font_family, size=10, color="333333")
    
    # Grid Borders
    thin_border_side = Side(style="thin", color="D3D3D3")
    thin_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    
    # Fill Colors
    bcg_green_fill = PatternFill(start_color="00A651", end_color="00A651", fill_type="solid")
    alt_row_fill = PatternFill(start_color="F7FAF8", end_color="F7FAF8", fill_type="solid")
    summary_bg_fill = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
    
    # Header Font
    header_font = Font(name=font_family, size=11, bold=True, color="FFFFFF")
    
    # -------------------------------------------------------------
    # Sheet 1: Summary
    # -------------------------------------------------------------
    ws_summary = wb.active
    ws_summary.title = "Summary"
    ws_summary.views.sheetView[0].showGridLines = True
    
    # Title Block
    ws_summary["A1"] = "Unilever Insights Report"
    ws_summary["A1"].font = title_font
    
    ws_summary["A3"] = "Metadata"
    ws_summary["A3"].font = section_font
    
    metadata = [
        ("User Question", question),
        ("SQL Query", sql),
        ("Record Count", len(records)),
        ("Generated At", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    ]
    
    row_idx = 4
    for key, val in metadata:
        ws_summary.cell(row=row_idx, column=1, value=key).font = key_font
        ws_summary.cell(row=row_idx, column=1).alignment = Alignment(vertical="top")
        
        val_cell = ws_summary.cell(row=row_idx, column=2, value=val)
        val_cell.font = value_font
        val_cell.alignment = Alignment(wrap_text=True, vertical="top")
        
        # Style layout for visual comfort
        ws_summary.cell(row=row_idx, column=1).fill = summary_bg_fill
        ws_summary.cell(row=row_idx, column=1).border = thin_border
        val_cell.border = thin_border
        row_idx += 1
        
    # Auto-fit Summary Column widths
    ws_summary.column_dimensions["A"].width = 20
    ws_summary.column_dimensions["B"].width = 70
    ws_summary.row_dimensions[5].height = 60  # Give the SQL query row some height to show SQL text wrap
    
    # -------------------------------------------------------------
    # Sheet 2: Data
    # -------------------------------------------------------------
    ws_data = wb.create_sheet(title="Data")
    ws_data.views.sheetView[0].showGridLines = True
    
    if not records:
        ws_data["A1"] = "No records found matching the query."
        ws_data["A1"].font = Font(name=font_family, size=11, italic=True)
    else:
        # Columns names
        headers = list(records[0].keys())
        
        # Write headers
        for col_idx, header in enumerate(headers, 1):
            cell = ws_data.cell(row=1, column=col_idx, value=header)
            cell.font = header_font
            cell.fill = bcg_green_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = thin_border
            
        ws_data.row_dimensions[1].height = 25
        
        # Write records
        for r_idx, record in enumerate(records, 2):
            ws_data.row_dimensions[r_idx].height = 20
            is_alt = (r_idx % 2 == 1)
            
            for c_idx, header in enumerate(headers, 1):
                val = record[header]
                cell = ws_data.cell(row=r_idx, column=c_idx, value=val)
                cell.font = Font(name=font_family, size=10)
                cell.border = thin_border
                
                # Alternating fills
                if is_alt:
                    cell.fill = alt_row_fill
                
                # Alignments and number formats
                if isinstance(val, (int, float)):
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    if isinstance(val, float):
                        cell.number_format = '#,##0.00'
                    else:
                        cell.number_format = '#,##0'
                elif isinstance(val, datetime.date):
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                    cell.number_format = 'yyyy-mm-dd'
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")
                    
        # Auto-adjust column widths for Data sheet
        for col in ws_data.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or '')
                if cell.row == 1:
                    # Give headers a bit more space
                    val_str += "    " 
                max_len = max(max_len, len(val_str))
            # Pad and set bounds
            ws_data.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 40)
            
    wb.save(filepath)
    return filename

if __name__ == "__main__":
    import sys
    import json
    if len(sys.argv) > 1:
        # Run via JSON temp file passed by Go
        temp_file_path = sys.argv[1]
        try:
            with open(temp_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            fn = generate_excel(data["records"], data["question"], data["sql"], data["export_dir"])
            print(fn)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # Test generation
        test_records = [
            {"Product Name": "Dove Soap 4pk", "Category": "Personal Care", "Revenue": 14250.50, "Units Sold": 350},
            {"Product Name": "Lipton Yellow Label 100s", "Category": "Beverages", "Revenue": 8900.20, "Units Sold": 410},
            {"Product Name": "Rexona Roll-On", "Category": "Personal Care", "Revenue": 5100.00, "Units Sold": 120},
        ]
        fn = generate_excel(test_records, "What are the sales per  product?", "SELECT name, cat, rev, units FROM sales", "./test_exports")
        print(f"Generated test file: {fn}")
