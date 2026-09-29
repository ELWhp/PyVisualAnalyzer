import sys, os, json, re
from datetime import datetime
import pandas as pd
from difflib import SequenceMatcher
from pathlib import Path
from PySide6.QtWidgets import (QApplication, QMainWindow, QTabWidget, QWidget,
                               QVBoxLayout, QHBoxLayout, QPushButton    , QTableWidget,
                               QTableWidgetItem, QFileDialog, QLabel, QComboBox,
                               QHeaderView, QLineEdit, QProgressBar, QSplitter,
                               QGroupBox, QMessageBox, QInputDialog, QDialog, QCheckBox, QCompleter, QSizePolicy)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QBrush, QDoubleValidator, QIntValidator

import data_handler as dh
import combinator
import harmonizer
import analyzer

SETTINGS_FILE = "settings.json"


def get_last_dir(key):
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)
            path = settings.get(key, "")
            if os.path.exists(path) or key == "last_saved_project_file": return path
    except:
        pass
    return str(Path.home() / "Documents")


def set_last_dir(key, path):
    settings = {}
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                settings = json.load(f)
        except:
            pass
    settings[key] = path
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f)


def shorten_path(full_path):
    if not full_path: return ""
    parts = os.path.normpath(full_path).split(os.sep)
    if len(parts) <= 2: return full_path
    short_parts = []
    for i in range(len(parts) - 2):
        p = parts[i]
        if len(p) > 6 and not p.endswith(':'):
            short_parts.append(p[:3] + "...")
        else:
            short_parts.append(p)
    short_parts.append(parts[-2])
    short_parts.append(parts[-1])
    return os.sep.join(short_parts)


def create_searchable_cb(items):
    cb = QComboBox()
    cb.addItems(items)
    cb.setEditable(True)
    cb.setInsertPolicy(QComboBox.NoInsert)
    cb.completer().setCompletionMode(QCompleter.PopupCompletion)
    cb.completer().setFilterMode(Qt.MatchContains)
    return cb


class SummaryViewer(QDialog):
    def __init__(self, parent, summary_map):
        super().__init__(parent)
        self.parent_ref = parent
        self.setWindowTitle("📊 Live Summary Viewer")
        self.resize(1100, 400)
        self.setAttribute(Qt.WA_DeleteOnClose)

        self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)

        layout = QVBoxLayout(self)
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)

        if summary_map:
            df = pd.DataFrame(list(summary_map.values()))
            self.table.setColumnCount(len(df.columns))
            self.table.setRowCount(len(df.index))
            self.table.setHorizontalHeaderLabels(list(df.columns))
            for r in range(len(df.index)):
                for c in range(len(df.columns)):
                    self.table.setItem(r, c, QTableWidgetItem(str(df.iloc[r, c])))
            self.table.resizeColumnsToContents()
        else:
            self.table.setColumnCount(1)
            self.table.setHorizontalHeaderLabels(["Status"])
            self.table.insertRow(0)
            self.table.setItem(0, 0, QTableWidgetItem("No summary data exists. Prepare/Analyze first."))

        layout.addWidget(self.table)

    def closeEvent(self, event):
        self.parent_ref.restore_ui_from_summary_view()
        event.accept()


class GapFillDialog(QDialog):
    def __init__(self, pending_cols, current_rules, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ Missing Data Treatment Settings")
        self.resize(600, 700)
        self.cols = sorted(list(pending_cols))
        self.rules = current_rules
        self.combos = {}
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        info = QLabel("Define how missing data (NaN) should be handled for each variable.")
        layout.addWidget(info)
        self.table = QTableWidget(len(self.cols), 3)
        self.table.setHorizontalHeaderLabels(["", "Variable Name", "Treatment Strategy"])
        self.table.setColumnWidth(0, 40)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setColumnWidth(2, 150)
        for r, col_name in enumerate(self.cols):
            btn_del = QPushButton("❌")
            btn_del.setStyleSheet("color: red; border: none; background: transparent;")
            btn_del.clicked.connect(lambda _, c=col_name: self.remove_rule_row(c))
            self.table.setCellWidget(r, 0, btn_del)
            item = QTableWidgetItem(col_name)
            if col_name not in self.rules:
                item.setBackground(QBrush(QColor("#2d6a4f")))
                item.setForeground(QBrush(QColor("white")))
            self.table.setItem(r, 1, item)
            cb = QComboBox();
            cb.addItems(["Interpolation", "Last Number"])
            cb.setCurrentText(self.rules.get(col_name, "Interpolation"))
            self.table.setCellWidget(r, 2, cb)
            self.combos[col_name] = cb
        layout.addWidget(self.table)
        btn_save = QPushButton("💾 Save & Continue");
        btn_save.clicked.connect(self.accept)
        layout.addWidget(btn_save)

    def remove_rule_row(self, col_name):
        for r in range(self.table.rowCount()):
            if self.table.item(r, 1).text() == col_name:
                self.table.removeRow(r)
                if col_name in self.combos: del self.combos[col_name]
                if col_name in self.rules: del self.rules[col_name]
                break

    def get_updated_rules(self):
        for col_name, cb in self.combos.items(): self.rules[col_name] = cb.currentText()
        return self.rules


class UniversalAnalyzer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Universal Data Analyzer - Professional Pipeline")
        self.resize(1650, 950)

        self.config_slots = ["Scale_Weight_Method", "Adjusted_Weight_Method", "Estimated_RMC_Method"]
        self.single_slots = ["Bone_Dry_Weight", "Start_Weight", "End_Weight", "Start_RMC", "End_RMC"]
        self.ts_slots = [
            "Mac_Cycle", "Raw_Scale_Weight", "Inlet_Vaisala_Temp", "Inlet_Vaisala_Humidity", "Exhaust_Vaisala_Temp",
            "Exhaust_Vaisala_Humidity", "Inlet_Vaisala_MR", "Exhaust_Vaisala_MR", "Power_Phase_1", "Power_Phase_2"
        ]
        self.custom_slots = []

        self.weight_opts = ["1. Use Raw Scale Data", "2. Filter Scale Data"]
        self.adjust_opts = ["1. Use Scale_Weight_Method", "2. Use Vaisalas & Start/End Weight",
                            "3. Use Scale_Weight_Method & Start/End Weight"]
        self.rmc_opts = ["1. Use Adjusted_Weight_Method & Bone Dry Weight",
                         "2. Use Adjusted_Weight_Method & Start/End RMC"]

        self.project_data = {"files": [], "file_types": {}, "pairs": [], "analysis_mode": "Merge DAQ + MAC",
                             "master_template": {}, "individual_mappings": {}, "alpha_filter": "0.95",
                             "custom_script": ""}
        self.raw_headers = {}
        self.combined_map = {}
        self.analyzed_map = {}
        self.summary_map = {}
        self.tuning_df = pd.DataFrame()

        self.gap_rules = {}
        self.tdms_rules = {}

        if os.path.exists("gap_rules.json"):
            try:
                with open("gap_rules.json", 'r') as f:
                    self.gap_rules = json.load(f)
            except:
                pass
        self.load_tdms_rules()
        self.load_grid_settings()
        self.init_ui()

        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self.trigger_autosave)
        self.update_autosave_timer()

        autosave_path = os.path.join(get_last_dir("dir_project"), ".autosave.uda")
        if os.path.exists(autosave_path):
            self.load_project_file(autosave_path, silent=True)
        else:
            last_proj = get_last_dir("last_saved_project_file")
            if last_proj and os.path.exists(last_proj) and last_proj.endswith('.uda'):
                self.load_project_file(last_proj, silent=True)

        self.apply_default_master_template()

    def load_tdms_rules(self):
        if os.path.exists("tdms_rules.json"):
            try:
                with open("tdms_rules.json", 'r') as f:
                    self.tdms_rules = json.load(f)
            except:
                pass

    def load_grid_settings(self):
        if os.path.exists("grid_settings.json"):
            try:
                with open("grid_settings.json", "r") as f:
                    data = json.load(f)
                    self.custom_slots = data.get("custom_slots", [])
                    self.project_data["master_template"] = data.get("master_template", {})
                    self.project_data["alpha_filter"] = data.get("alpha_filter", "0.95")
                    self.project_data["custom_script"] = data.get("custom_script", "")
            except:
                pass

    def apply_default_master_template(self):
        defaults = {
            "Scale_Weight_Method": "1. Use Raw Scale Data",
            "Adjusted_Weight_Method": "1. Use Scale_Weight_Method",
            "Estimated_RMC_Method": "1. Use Adjusted_Weight_Method & Bone Dry Weight",
            "Bone_Dry_Weight_src": "Tuning Book", "Start_Weight_src": "Tuning Book", "End_Weight_src": "Tuning Book",
            "Start_RMC_src": "Tuning Book", "End_RMC_src": "Tuning Book",
            "Bone_Dry_Weight_unit": "lb", "Start_Weight_unit": "lb", "End_Weight_unit": "lb",
            "Power_Phase_1_unit": "W", "Power_Phase_2_unit": "W",
        }
        for k, v in defaults.items():
            if k not in self.project_data["master_template"]:
                self.project_data["master_template"][k] = v

        script_dir = os.path.join(os.getcwd(), "custom_scripts")
        if not os.path.exists(script_dir): os.makedirs(script_dir)
        default_script = os.path.join(script_dir, "custom_metrics.py")
        if not os.path.exists(default_script):
            with open(default_script, "w") as f:
                f.write(
                    'import pandas as pd\n\ndef run_custom_math(df, summary_row):\n    targets = [6.0, 2.0, 1.0]\n    for target in targets:\n        if "Estimated_RMC" in df.columns and "Common_Time_Sec" in df.columns:\n            hit = df[df["Estimated_RMC"] <= target]\n            if not hit.empty:\n                summary_row[f"Time_to_{int(target)}_RMC_Seconds"] = hit["Common_Time_Sec"].iloc[0]\n                if "Percent_Conductivity" in df.columns:\n                    summary_row[f"Conductivity_at_{int(target)}_RMC"] = hit["Percent_Conductivity"].iloc[0]\n                elif "Percent Conductivity" in df.columns:\n                    summary_row[f"Conductivity_at_{int(target)}_RMC"] = hit["Percent Conductivity"].iloc[0]\n                else:\n                    summary_row[f"Conductivity_at_{int(target)}_RMC"] = "Col not mapped"\n            else:\n                summary_row[f"Time_to_{int(target)}_RMC_Seconds"] = f"Never reached {target}%"\n                summary_row[f"Conductivity_at_{int(target)}_RMC"] = "N/A"\n    return df, summary_row\n')

        if not self.project_data.get("custom_script") or not os.path.exists(self.project_data.get("custom_script", "")):
            self.project_data["custom_script"] = default_script
            if hasattr(self, 'btn_custom_script'):
                self.btn_custom_script.setText(f"Script: custom_metrics.py")

    def save_grid_settings_dialog(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save Grid Config", get_last_dir("dir_grid"), "UDA Config (*.uda)")
        if p:
            set_last_dir("dir_grid", os.path.dirname(p))
            with open(p, "w") as f:
                json.dump({"custom_slots": self.custom_slots, "master_template": self.project_data["master_template"],
                           "alpha_filter": self.project_data.get("alpha_filter", "0.95"),
                           "custom_script": self.project_data.get("custom_script", "")}, f, indent=4)
            QMessageBox.information(self, "Saved", "Grid Template saved.")

    def load_grid_settings_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "Load Grid Config", get_last_dir("dir_grid"), "UDA Config (*.uda)")
        if p:
            set_last_dir("dir_grid", os.path.dirname(p))
            with open(p, "r") as f:
                data = json.load(f)
            self.custom_slots = data.get("custom_slots", [])
            self.project_data["master_template"] = data.get("master_template", {})
            self.project_data["alpha_filter"] = data.get("alpha_filter", "0.95")
            self.alpha_input.setText(self.project_data["alpha_filter"])
            self.project_data["custom_script"] = data.get("custom_script", "")
            if self.project_data["custom_script"]:
                self.btn_custom_script.setText(f"Script: {os.path.basename(self.project_data['custom_script'])}")
            else:
                self.btn_custom_script.setText("📂 Select Post-Processing Script")
            self.apply_default_master_template()
            self.rebuild_grid()

    def save_grid_settings(self):
        with open("grid_settings.json", "w") as f:
            json.dump({"custom_slots": self.custom_slots, "master_template": self.project_data["master_template"],
                       "alpha_filter": self.project_data.get("alpha_filter", "0.95"),
                       "custom_script": self.project_data.get("custom_script", "")}, f, indent=4)

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        self.menu_bar = QHBoxLayout()
        btn_clear = QPushButton("🧹 Clear Data")
        btn_clear.setStyleSheet("background-color: #c0392b; color: white; font-weight: bold;")
        btn_clear.clicked.connect(self.clear_data)

        btn_save = QPushButton("💾 Save Project")
        btn_save.clicked.connect(lambda: self.save_project(silent=False))

        btn_load_proj = QPushButton("📂 Load Project")
        btn_load_proj.clicked.connect(self.load_project_dialog)

        self.chk_autosave = QCheckBox("Autosave on")
        self.chk_autosave.setChecked(True)
        self.chk_autosave.stateChanged.connect(self.update_autosave_timer)

        lbl_autosave = QLabel("each:")
        self.txt_autosave_interval = QLineEdit("5")
        self.txt_autosave_interval.setValidator(QIntValidator(1, 60))
        self.txt_autosave_interval.setFixedWidth(30)
        self.txt_autosave_interval.textChanged.connect(self.update_autosave_timer)
        lbl_mins = QLabel("minutes")

        self.menu_bar.addWidget(btn_clear);
        self.menu_bar.addWidget(btn_save);
        self.menu_bar.addWidget(btn_load_proj)
        self.menu_bar.addSpacing(20)
        self.menu_bar.addWidget(self.chk_autosave);
        self.menu_bar.addWidget(lbl_autosave)
        self.menu_bar.addWidget(self.txt_autosave_interval);
        self.menu_bar.addWidget(lbl_mins)
        self.menu_bar.addStretch()
        main_layout.addLayout(self.menu_bar)

        self.tabs = QTabWidget()

        # TAB 1: Combinator
        self.sort_tab = QWidget();
        sort_layout = QHBoxLayout(self.sort_tab)
        self.sort_splitter = QSplitter(Qt.Horizontal)

        left_sort = QWidget();
        ls_layout = QVBoxLayout(left_sort)
        btn_load_logs = QPushButton("➕ Load Data Logs (.mac / .csv / .tdms)")
        btn_load_logs.setStyleSheet("background-color: #3498db; color: white; font-weight: bold; padding: 10px;")
        btn_load_logs.clicked.connect(self.bulk_load)

        self.file_table = QTableWidget(0, 3)
        self.file_table.setHorizontalHeaderLabels(["", "Tags", "Loaded File"])
        self.file_table.setColumnWidth(0, 40);
        self.file_table.setColumnWidth(1, 70)
        self.file_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        ls_layout.addWidget(btn_load_logs);
        ls_layout.addWidget(QLabel("<b>Step 1: File Roster</b>"));
        ls_layout.addWidget(self.file_table)

        right_sort = QWidget();
        rs_layout = QVBoxLayout(right_sort)

        mode_layout = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Merge DAQ + MAC", "Analyze DAQ Only", "Analyze MAC Only"])
        self.mode_combo.currentTextChanged.connect(self.change_mode)
        mode_layout.addWidget(QLabel("<b>Analysis Mode:</b>"));
        mode_layout.addWidget(self.mode_combo)

        btn_harmonizer = QPushButton("⚙️ TDMS Harmonizer Rules")
        btn_harmonizer.setStyleSheet("background-color: #8e44ad; color: white; font-weight: bold;")
        btn_harmonizer.clicked.connect(lambda: self.open_harmonizer())
        mode_layout.addWidget(btn_harmonizer);
        mode_layout.addStretch()
        rs_layout.addLayout(mode_layout)

        self.step2_widget = QWidget();
        step2_layout = QVBoxLayout(self.step2_widget);
        step2_layout.setContentsMargins(0, 0, 0, 0)

        tb_group = QGroupBox("Tuning Book Auto-Pairing Engine");
        tb_layout = QVBoxLayout(tb_group)
        tb_row1 = QHBoxLayout();
        btn_load_tb = QPushButton("📂 Upload Tuning Book")
        btn_load_tb.clicked.connect(self.load_tuning_book)
        self.tb_path_display = QLineEdit();
        self.tb_path_display.setReadOnly(True)
        tb_row1.addWidget(btn_load_tb);
        tb_row1.addWidget(self.tb_path_display)
        tb_row2 = QHBoxLayout();
        self.cb_tb_daq = create_searchable_cb([]);
        self.cb_tb_mac = create_searchable_cb([])
        btn_update_table = QPushButton("🔄 Update Table")
        btn_update_table.clicked.connect(self.auto_pair_from_tuning)
        tb_row2.addWidget(QLabel("DAQ Col:"));
        tb_row2.addWidget(self.cb_tb_daq)
        tb_row2.addWidget(QLabel("MAC Col:"));
        tb_row2.addWidget(self.cb_tb_mac);
        tb_row2.addWidget(btn_update_table)
        tb_layout.addLayout(tb_row1);
        tb_layout.addLayout(tb_row2);
        step2_layout.addWidget(tb_group)

        btn_automatch = QPushButton("🤖 Auto-Match Files")
        btn_automatch.setStyleSheet(
            "background-color: #e67e22; color: white; font-weight: bold; font-size: 14px; padding: 5px;")
        btn_automatch.clicked.connect(self.auto_match_files)
        step2_layout.addWidget(btn_automatch)

        self.lbl_match_warning = QLabel("")
        self.lbl_match_warning.setStyleSheet(
            "background-color: #5A1818; color: white; font-weight: bold; padding: 6px; border-radius: 4px;")
        self.lbl_match_warning.hide()
        step2_layout.addWidget(self.lbl_match_warning)

        self.btn_add_pair = QPushButton("➕ Manually Add Cycle Pair")
        self.btn_add_pair.clicked.connect(self.add_pair_row)

        self.pair_table = QTableWidget(0, 5)
        self.pair_table.setHorizontalHeaderLabels(["", "Fuel", "Cycle Name", "DAQ File", "MAC File"])
        self.pair_table.setColumnWidth(0, 40);
        self.pair_table.setColumnWidth(1, 110);
        self.pair_table.setColumnWidth(2, 200);
        self.pair_table.setColumnWidth(3, 300);
        self.pair_table.setColumnWidth(4, 300)
        self.pair_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Interactive);
        self.pair_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Interactive);
        self.pair_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Interactive)

        step2_layout.addWidget(QLabel("<b>Step 2: Assign Cycles</b>"))
        step2_layout.addWidget(self.btn_add_pair);
        step2_layout.addWidget(self.pair_table)
        rs_layout.addWidget(self.step2_widget)

        exec_group = QGroupBox("Execution");
        exec_lay = QVBoxLayout(exec_group)
        self.btn_gap_rules = QPushButton("⚙️ Missing Data Treatment Settings")
        self.btn_gap_rules.setStyleSheet("background-color: #f39c12; color: white; font-weight: bold; height: 35px;")
        self.btn_gap_rules.clicked.connect(lambda: self.open_gap_fill_settings())
        exec_lay.addWidget(self.btn_gap_rules)

        btn_row = QHBoxLayout()
        self.btn_combine = QPushButton("🧹 PREPARE DATA")
        self.btn_combine.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold; height: 50px;")
        self.btn_combine.clicked.connect(self.execute_combinator)
        self.btn_export_csvs = QPushButton("💾 Export Individual Cycle CSVs")
        self.btn_export_csvs.setStyleSheet("background-color: #34495e; color: white; font-weight: bold; height: 50px;")
        self.btn_export_csvs.setEnabled(False);
        self.btn_export_csvs.clicked.connect(self.save_individual_csvs)
        btn_row.addWidget(self.btn_combine);
        btn_row.addWidget(self.btn_export_csvs)
        exec_lay.addLayout(btn_row)
        rs_layout.addWidget(exec_group)

        self.sort_splitter.addWidget(left_sort);
        self.sort_splitter.addWidget(right_sort);
        self.sort_splitter.setSizes([500, 1100])
        sort_layout.addWidget(self.sort_splitter)

        # TAB 2: MASTER GRID
        self.map_tab = QWidget();
        map_layout = QVBoxLayout(self.map_tab)

        self.top_controls_widget = QWidget()
        top_controls = QHBoxLayout(self.top_controls_widget)
        top_controls.setContentsMargins(0, 0, 0, 0)
        btn_add_col = QPushButton("➕ Add Custom Column");
        btn_add_col.clicked.connect(self.add_custom_column)
        btn_rem_col = QPushButton("➖ Remove Column");
        btn_rem_col.clicked.connect(self.remove_custom_column)

        top_controls.addWidget(btn_add_col);
        top_controls.addWidget(btn_rem_col)
        top_controls.addSpacing(30)
        top_controls.addWidget(QLabel("<b>IIR Alpha Filter (0.0 - 1.0):</b>"))
        self.alpha_input = QLineEdit()
        self.alpha_input.setValidator(QDoubleValidator(0.001, 0.999, 4))
        self.alpha_input.setText(self.project_data.get("alpha_filter", "0.95"))
        self.alpha_input.textChanged.connect(self.update_alpha)
        self.alpha_input.setFixedWidth(60)
        top_controls.addWidget(self.alpha_input)

        btn_save_grid = QPushButton("💾 Save Grid Config")
        btn_save_grid.clicked.connect(self.save_grid_settings_dialog)
        btn_load_grid = QPushButton("📂 Load Grid Config")
        btn_load_grid.clicked.connect(self.load_grid_settings_dialog)
        top_controls.addStretch();
        top_controls.addWidget(btn_save_grid);
        top_controls.addWidget(btn_load_grid)
        map_layout.addWidget(self.top_controls_widget)

        self.map_splitter = QSplitter(Qt.Vertical)

        # 1. Master Grid
        self.grid = QTableWidget()
        self.grid.cellClicked.connect(self.on_grid_row_clicked)
        self.map_splitter.addWidget(self.grid)

        # 2. Data Monitor
        self.monitor_group = QGroupBox("🔍 Data Monitor")
        mon_lay = QVBoxLayout(self.monitor_group)
        self.monitor_table = QTableWidget()
        self.monitor_table.setStyleSheet("background-color: #111; color: white; gridline-color: #444;")
        self.monitor_table.setEditTriggers(QTableWidget.NoEditTriggers)
        mon_lay.addWidget(self.monitor_table)
        self.map_splitter.addWidget(self.monitor_group)

        self.map_splitter.setSizes([700, 150])
        map_layout.addWidget(self.map_splitter)

        self.bottom_controls_widget = QWidget()
        bottom_controls = QHBoxLayout(self.bottom_controls_widget)
        bottom_controls.setContentsMargins(0, 0, 0, 0)

        self.btn_import_combined = QPushButton("📂 Import _Combined Data")
        self.btn_import_combined.setStyleSheet(
            "background-color: #2980b9; color: white; font-weight: bold; padding: 10px;")
        self.btn_import_combined.clicked.connect(self.import_combined_data)

        self.btn_import_summary = QPushButton("📂 Import Summary File")
        self.btn_import_summary.setStyleSheet(
            "background-color: #f1c40f; color: black; font-weight: bold; padding: 10px;")
        self.btn_import_summary.clicked.connect(self.import_summary_file)

        self.btn_export_summary = QPushButton("📄 Export Summary")
        self.btn_export_summary.setStyleSheet(
            "background-color: #c0392b; color: white; font-weight: bold; padding: 10px;")
        self.btn_export_summary.clicked.connect(self.export_summary_file)

        self.btn_view_summary = QPushButton("📊 View Live Summary")
        self.btn_view_summary.setStyleSheet(
            "background-color: #16a085; color: white; font-weight: bold; padding: 10px;")
        self.btn_view_summary.clicked.connect(self.open_summary_view)

        self.btn_custom_script = QPushButton("📂 Select Post-Processing Script")
        self.btn_custom_script.setStyleSheet(
            "background-color: #e67e22; color: white; font-weight: bold; padding: 10px;")
        if self.project_data.get("custom_script", ""):
            self.btn_custom_script.setText(f"Script: {os.path.basename(self.project_data['custom_script'])}")
        self.btn_custom_script.clicked.connect(self.select_custom_script)

        self.btn_analyze_data = QPushButton("🔬 ANALYZE DATA")
        self.btn_analyze_data.setStyleSheet(
            "background-color: #8e44ad; color: white; font-weight: bold; padding: 10px;")
        self.btn_analyze_data.clicked.connect(self.execute_analyzer)

        self.btn_export_analyzed = QPushButton("💾 Export Analyzed")
        self.btn_export_analyzed.setStyleSheet(
            "background-color: #27ae60; color: white; font-weight: bold; padding: 10px;")
        self.btn_export_analyzed.clicked.connect(self.export_analyzed_csvs)

        bottom_controls.addWidget(self.btn_import_combined)
        bottom_controls.addWidget(self.btn_import_summary)
        bottom_controls.addWidget(self.btn_export_summary)
        bottom_controls.addWidget(self.btn_view_summary)
        bottom_controls.addStretch()
        bottom_controls.addWidget(self.btn_custom_script)
        bottom_controls.addWidget(self.btn_analyze_data)
        bottom_controls.addWidget(self.btn_export_analyzed)
        map_layout.addWidget(self.bottom_controls_widget)

        # TAB 3: CYCLE VISUALIZER
        self.graph_tab = QWidget();
        graph_layout = QVBoxLayout(self.graph_tab)
        graph_layout.addWidget(QLabel("<h2>Cycle Visualizer (Graphing Tab Placeholder)</h2>"))
        graph_layout.addWidget(QLabel("Interactive graphing features will populate here..."))
        graph_layout.addStretch()

        self.tabs.addTab(self.sort_tab, "1. Combinator");
        self.tabs.addTab(self.map_tab, "2. Master Grid")
        self.tabs.addTab(self.graph_tab, "3. Cycle Visualizer")

        self.tabs.currentChanged.connect(self.on_tab_change)
        main_layout.addWidget(self.tabs)
        self.progress = QProgressBar();
        main_layout.addWidget(self.progress)

    def closeEvent(self, event):
        msg = QMessageBox(self)
        msg.setWindowTitle("Exit Application")
        msg.setText("You are about to close the application. What would you like to do?")
        btn_files = msg.addButton("Save Project and Files", QMessageBox.AcceptRole)
        btn_proj = msg.addButton("Save Project", QMessageBox.AcceptRole)
        btn_close = msg.addButton("Just Close", QMessageBox.DestructiveRole)
        btn_cancel = msg.addButton("Cancel", QMessageBox.RejectRole)
        msg.exec()

        if msg.clickedButton() == btn_cancel:
            event.ignore()
            return

        if msg.clickedButton() in [btn_files, btn_proj]:
            self.save_project(silent=True)
            if msg.clickedButton() == btn_files:
                self.export_all_data_silently()
        event.accept()

    def open_summary_view(self):
        self.tabs.tabBar().setEnabled(False)
        self.top_controls_widget.setEnabled(False)
        self.bottom_controls_widget.setEnabled(False)
        for i in range(self.menu_bar.count()):
            widget = self.menu_bar.itemAt(i).widget()
            if widget: widget.setEnabled(False)

        self.summary_viewer = SummaryViewer(self, self.summary_map)
        self.summary_viewer.show()

    def restore_ui_from_summary_view(self):
        self.tabs.tabBar().setEnabled(True)
        self.top_controls_widget.setEnabled(True)
        self.bottom_controls_widget.setEnabled(True)
        for i in range(self.menu_bar.count()):
            widget = self.menu_bar.itemAt(i).widget()
            if widget: widget.setEnabled(True)

    def select_custom_script(self):
        script_dir = os.path.join(os.getcwd(), "custom_scripts")
        if not os.path.exists(script_dir): os.makedirs(script_dir)
        p, _ = QFileDialog.getOpenFileName(self, "Select Post-Processing Script", script_dir, "Python Files (*.py)")
        if p:
            self.project_data["custom_script"] = p
            self.save_grid_settings()
            self.btn_custom_script.setText(f"Script: {os.path.basename(p)}")

    def export_all_data_silently(self):
        folder = get_last_dir("dir_export_csvs")
        ts = datetime.now().strftime("%m%d%y%H%M%S")
        if self.combined_map:
            for name, df in self.combined_map.items():
                df.to_csv(os.path.join(folder, f"{name}_{ts}.csv"), index=False)
        if self.analyzed_map:
            for name, df in self.analyzed_map.items():
                export_name = name.replace("_Combined", f"_Analyzed")
                df.to_csv(os.path.join(folder, f"{export_name}_{ts}.csv"), index=False)
        if self.summary_map:
            pd.DataFrame(list(self.summary_map.values())).to_csv(os.path.join(folder, f"summary_{ts}.csv"), index=False)

    def update_autosave_timer(self):
        if self.chk_autosave.isChecked():
            try:
                mins = int(self.txt_autosave_interval.text())
                if mins < 1: mins = 1
                if mins > 60: mins = 60
                self.autosave_timer.start(mins * 60 * 1000)
            except:
                pass
        else:
            self.autosave_timer.stop()

    def trigger_autosave(self):
        path = os.path.join(get_last_dir("dir_project"), ".autosave.uda")
        self.save_project_file(path, silent=True, is_autosave=True)

    def clear_data(self):
        reply = QMessageBox.question(self, "Clear Data",
                                     "Are you sure you want to wipe current files and matches from memory? Config rules will remain.",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.project_data["files"] = []
            self.project_data["file_types"] = {}
            self.project_data["pairs"] = []
            self.project_data["individual_mappings"] = {}
            self.project_data["imported_combined"] = []
            self.raw_headers = {}
            self.combined_map = {}
            self.analyzed_map = {}
            self.summary_map = {}
            self.tuning_df = pd.DataFrame()
            self.tb_path_display.clear()
            self.apply_default_master_template()
            self.rebuild_file_table()
            self.refresh_pairing_table()
            self.rebuild_grid()

    def update_alpha(self, text):
        self.project_data["alpha_filter"] = text
        self.save_grid_settings()

    def open_harmonizer(self, sample_file=None):
        dialog = harmonizer.TDMSHarmonizerDialog(self, sample_file)
        dialog.exec()
        self.load_tdms_rules()

    def auto_tag_file(self, f_path):
        f_low = os.path.basename(f_path).lower()
        if 'mac' in f_low or f_low.endswith('.mac'): return "MAC"
        if 'daq' in f_low or f_low.endswith('.tdms'): return "DAQ"
        try:
            df = dh.load_dataframe(f_path, nrows=5)
            cols = [str(c).lower() for c in df.columns]
            if any(x in cols for x in ["mac_cycle", "door_status", "systemstate", "mac cycle"]): return "MAC"
            if any(x in cols for x in ["tc 1", "l1 voltage", "run time (sec)", "p1 volt ln (v)"]): return "DAQ"
        except:
            pass
        return "RAW"

    def bulk_load(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Logs", get_last_dir("dir_logs"))
        if files:
            set_last_dir("dir_logs", os.path.dirname(files[0]))
            harmonizer_opened = False
            for f in files:
                f_low = os.path.basename(f).lower()
                if f_low.endswith('.tdms_index'): continue
                if f not in self.project_data["files"]:
                    self.project_data["files"].append(f)
                    self.project_data["file_types"][f] = self.auto_tag_file(f)
                    try:
                        if f_low.endswith('.tdms') and not harmonizer_opened and dh.check_tdms_needs_harmonization(f,
                                                                                                                   self.tdms_rules):
                            self.open_harmonizer(sample_file=f)
                            harmonizer_opened = True
                        df = dh.load_dataframe(f, nrows=1, tdms_rules=self.tdms_rules)
                        if not df.empty: self.raw_headers[f] = list(df.columns)
                    except:
                        pass
            self.rebuild_file_table();
            self.refresh_pairing_table()

    def detect_fuel_type(self, text):
        nl = text.lower()
        if "heatpump" in nl or "hhp" in nl: return "HP"
        if "gas" in nl: return "G"
        if "elec" in nl: return "E"
        m = re.search(r'(\d+)\s*v', nl)
        if m and int(m.group(1)) < 170: return "G"
        return "E"

    def auto_match_files(self):
        daq_files = [f for f in self.project_data["files"] if self.project_data["file_types"].get(f) == "DAQ"]
        mac_files = [f for f in self.project_data["files"] if self.project_data["file_types"].get(f) == "MAC"]

        matched_pairs = []
        used_macs = set()

        def get_clean_core(name):
            base = os.path.basename(name)
            for ext in ['.tdms', '.mac', '.csv']:
                if base.lower().endswith(ext): base = base[:-len(ext)]
            if '_DAQ' in base: return base.split('_DAQ')[0]
            if '_MAC' in base: return base.split('_MAC')[0]
            base = re.sub(r'_\d{6,8}_\d{6}$', '', base)
            return base.strip()

        for d in daq_files:
            dbase = os.path.basename(d)
            core_d = get_clean_core(dbase)

            best_m = ""
            best_score = 0

            for m in mac_files:
                if m in used_macs: continue
                mbase = os.path.basename(m)
                core_m = get_clean_core(mbase)

                if core_d == core_m:
                    best_m = m
                    best_score = 1.0
                    break

                if core_d.startswith(core_m) or core_m.startswith(core_d):
                    best_m = m
                    best_score = 0.9

                score = SequenceMatcher(None, core_d.lower(), core_m.lower()).ratio()
                if score > best_score:
                    best_score = score
                    best_m = m

            if best_score > 0.4 and best_m:
                matched_pairs.append({"cycle": core_d, "daq": d, "mac": best_m, "tuning_data": {},
                                      "fuel_type": self.detect_fuel_type(d + best_m)})
                used_macs.add(best_m)
            else:
                matched_pairs.append(
                    {"cycle": core_d, "daq": d, "mac": "", "tuning_data": {}, "fuel_type": self.detect_fuel_type(d)})

        for m in mac_files:
            if m not in used_macs:
                matched_pairs.append({"cycle": get_clean_core(m), "daq": "", "mac": m, "tuning_data": {},
                                      "fuel_type": self.detect_fuel_type(m)})

        if matched_pairs:
            self.project_data["pairs"] = matched_pairs
            self.refresh_pairing_table()
        else:
            QMessageBox.warning(self, "Auto-Match", "Could not find any files to match.")

    def remove_file(self, f_path):
        if f_path in self.project_data["files"]: self.project_data["files"].remove(f_path)
        if f_path in self.project_data["file_types"]: del self.project_data["file_types"][f_path]
        if f_path in self.raw_headers: del self.raw_headers[f_path]
        for pair in self.project_data["pairs"]:
            if pair.get("daq") == f_path: pair["daq"] = ""
            if pair.get("mac") == f_path: pair["mac"] = ""
        self.rebuild_file_table();
        self.refresh_pairing_table()

    def remove_pair_row(self, pair_obj):
        if pair_obj in self.project_data["pairs"]:
            self.project_data["pairs"].remove(pair_obj)
            self.refresh_pairing_table()

    def create_fuel_widget(self, pair_obj):
        w = QWidget();
        lay = QHBoxLayout(w);
        lay.setContentsMargins(2, 2, 2, 2)
        btn_e = QPushButton("E");
        btn_g = QPushButton("G");
        btn_hp = QPushButton("HP")
        btn_e.setFixedSize(25, 25);
        btn_g.setFixedSize(25, 25);
        btn_hp.setFixedSize(25, 25)

        def set_style():
            gray = "background-color: #7f8c8d; color: white;"
            current_fuel = pair_obj.get("fuel_type", "E")
            btn_e.setStyleSheet(
                "background-color: #f39c12; color: black; font-weight: bold;" if current_fuel == "E" else gray)
            btn_g.setStyleSheet(
                "background-color: #2ecc71; color: black; font-weight: bold;" if current_fuel == "G" else gray)
            btn_hp.setStyleSheet(
                "background-color: #9b59b6; color: white; font-weight: bold;" if current_fuel == "HP" else gray)

        set_style()

        def on_click(f_type):
            if pair_obj.get("fuel_type", "E") == f_type:
                pair_obj["fuel_type"] = "Undefined"
            else:
                pair_obj["fuel_type"] = f_type
            set_style()

        btn_e.clicked.connect(lambda: on_click("E"))
        btn_g.clicked.connect(lambda: on_click("G"))
        btn_hp.clicked.connect(lambda: on_click("HP"))
        lay.addWidget(btn_e);
        lay.addWidget(btn_g);
        lay.addWidget(btn_hp)
        return w

    def rebuild_file_table(self):
        self.file_table.setRowCount(len(self.project_data["files"]))
        for row, f_path in enumerate(self.project_data["files"]):
            btn_remove = QPushButton("❌");
            btn_remove.setStyleSheet("color: red; border: none; background: transparent;")
            btn_remove.clicked.connect(lambda _, f=f_path: self.remove_file(f))
            wrapper = QWidget();
            lay = QHBoxLayout(wrapper);
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(btn_remove, alignment=Qt.AlignCenter);
            self.file_table.setCellWidget(row, 0, wrapper)
            self.file_table.setCellWidget(row, 1, self.create_tag_widget(f_path, row))
            item = QTableWidgetItem(os.path.basename(f_path))
            item.setBackground(QBrush(QColor("#2d2d2d")));
            item.setForeground(QBrush(QColor("white")))
            self.file_table.setItem(row, 2, item)

    def create_tag_widget(self, f, row):
        tag = self.project_data["file_types"].get(f, "")
        w = QWidget();
        lay = QHBoxLayout(w);
        lay.setContentsMargins(2, 2, 2, 2)
        btn_m = QPushButton("M");
        btn_d = QPushButton("D")
        btn_m.setFixedSize(25, 25);
        btn_d.setFixedSize(25, 25)
        gray = "background-color: #7f8c8d; color: white;"
        btn_m.setStyleSheet("background-color: orange; color: black;" if tag == "MAC" else gray)
        btn_d.setStyleSheet("background-color: #3498db; color: white;" if tag == "DAQ" else gray)
        btn_m.clicked.connect(lambda: self.set_tag(f, "MAC", row));
        btn_d.clicked.connect(lambda: self.set_tag(f, "DAQ", row))
        lay.addWidget(btn_m);
        lay.addWidget(btn_d)
        return w

    def set_tag(self, f, tag, row):
        self.project_data["file_types"][f] = tag;
        self.rebuild_file_table();
        self.refresh_pairing_table()

    def load_tuning_book(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Tuning Book", get_last_dir("dir_tuning"),
                                              "Data (*.csv *.xlsx *.xls)")
        if path:
            set_last_dir("dir_tuning", os.path.dirname(path))
            self.tb_path_display.setText(path)
            try:
                if path.endswith('.csv'):
                    with open(path, 'rb') as f:
                        self.tuning_df = pd.read_csv(io.StringIO(dh.sanitize_csv(f.read())))
                else:
                    self.tuning_df = pd.read_excel(path)
                headers = list(self.tuning_df.columns)
                self.cb_tb_daq.clear();
                self.cb_tb_mac.clear()
                self.cb_tb_daq.addItems(["-- Select --"] + headers);
                self.cb_tb_mac.addItems(["-- Select --"] + headers)
            except Exception as e:
                self.tb_path_display.setText(f"Err: {e}")

    def auto_pair_from_tuning(self):
        if self.tuning_df.empty: return
        dc, mc = self.cb_tb_daq.currentText(), self.cb_tb_mac.currentText()
        if "--" in dc or "--" in mc: return
        self.project_data["pairs"] = []
        for i, row in self.tuning_df.iterrows():
            t_daq, t_mac = str(row[dc]).strip(), str(row[mc]).strip()

            if t_daq.lower() in ["nan", "nat", "none", ""] or t_mac.lower() in ["nan", "nat", "none", ""]:
                continue

            f_daq = next((f for f in self.project_data["files"] if t_daq in f), "")
            f_mac = next((f for f in self.project_data["files"] if t_mac in f), "")

            if not f_daq and not f_mac:
                continue

            fuel_code = self.detect_fuel_type(f_daq + f_mac + t_daq + t_mac)
            self.project_data["pairs"].append(
                {"cycle": f"Cycle {i + 1}", "daq": f_daq, "mac": f_mac, "tuning_data": row.to_dict(),
                 "fuel_type": fuel_code})
        self.refresh_pairing_table()

    def add_pair_row(self):
        self.project_data["pairs"].append(
            {"cycle": f"Cycle {len(self.project_data['pairs']) + 1}", "daq": "", "mac": "", "tuning_data": {},
             "fuel_type": "E"})
        self.refresh_pairing_table()

    def refresh_pairing_table(self):
        self.pair_table.setRowCount(len(self.project_data["pairs"]))
        daq_f = [f for f in self.project_data["files"] if self.project_data["file_types"].get(f) == "DAQ"]
        mac_f = [f for f in self.project_data["files"] if self.project_data["file_types"].get(f) == "MAC"]
        mode = self.project_data.get("analysis_mode", "")

        for r, pair in enumerate(self.project_data["pairs"]):
            btn_remove = QPushButton("❌");
            btn_remove.setStyleSheet("color: red; border: none; background: transparent;")
            btn_remove.clicked.connect(lambda _, p=pair: self.remove_pair_row(p))
            self.pair_table.setCellWidget(r, 0, btn_remove)

            self.pair_table.setCellWidget(r, 1, self.create_fuel_widget(pair))
            self.pair_table.setItem(r, 2, QTableWidgetItem(pair.get("cycle", f"Cycle {r + 1}")))

            c_d = create_searchable_cb(["-- Select DAQ --", ""] + [shorten_path(f) for f in daq_f])
            for i, f in enumerate(daq_f): c_d.setItemData(i + 2, f)
            if pair.get("daq"):
                idx = c_d.findData(pair.get("daq"))
                if idx >= 0: c_d.setCurrentIndex(idx)
            c_d.currentIndexChanged.connect(lambda idx, row=r, cb=c_d: self.update_pair(row, "daq", cb))
            self.pair_table.setCellWidget(r, 3, c_d)

            c_m = create_searchable_cb(["-- Select MAC --", ""] + [shorten_path(f) for f in mac_f])
            for i, f in enumerate(mac_f): c_m.setItemData(i + 2, f)
            if pair.get("mac"):
                idx = c_m.findData(pair.get("mac"))
                if idx >= 0: c_m.setCurrentIndex(idx)
            c_m.currentIndexChanged.connect(lambda idx, row=r, cb=c_m: self.update_pair(row, "mac", cb))
            self.pair_table.setCellWidget(r, 4, c_m)

        if "Merge" in mode:
            self.pair_table.showColumn(3); self.pair_table.showColumn(4)
        elif "DAQ Only" in mode:
            self.pair_table.showColumn(3); self.pair_table.hideColumn(4)
        elif "MAC Only" in mode:
            self.pair_table.hideColumn(3); self.pair_table.showColumn(4)

    def update_pair(self, row, key, cb):
        val = cb.currentData()
        self.project_data["pairs"][row][key] = val
        if key == 'daq' and val:
            new_name = f"{os.path.basename(val).split('.')[0]}_{row + 1}"
            self.pair_table.item(row, 2).setText(new_name);
            self.project_data["pairs"][row]['cycle'] = new_name

    def change_mode(self, t):
        self.project_data["analysis_mode"] = t
        self.refresh_pairing_table()

    def open_gap_fill_settings(self, required_cols=None):
        if required_cols is None:
            required_cols = set()
            for f, headers in self.raw_headers.items():
                prefix = self.project_data["file_types"].get(f, "Raw").capitalize()
                required_cols.update([c if str(c).startswith(f"{prefix}.") else f"{prefix}.{c}" for c in headers])
        dialog = GapFillDialog(required_cols, self.gap_rules, self)
        if dialog.exec():
            self.gap_rules = dialog.get_updated_rules()
            with open("gap_rules.json", "w") as f: json.dump(self.gap_rules, f, indent=4)
            return True
        return False

    def execute_combinator(self):
        required_cols = set();
        mode = self.project_data["analysis_mode"]
        if "Merge" in mode:
            for p in self.project_data["pairs"]:
                dp, mp = p.get("daq"), p.get("mac")
                if dp and dp in self.raw_headers: required_cols.update(
                    [c if str(c).startswith("Daq.") else f"Daq.{c}" for c in self.raw_headers[dp]])
                if mp and mp in self.raw_headers: required_cols.update(
                    [c if str(c).startswith("Mac.") else f"Mac.{c}" for c in self.raw_headers[mp]])
        else:
            tag = "DAQ" if "DAQ" in mode else "MAC"
            for f in self.project_data["files"]:
                if self.project_data["file_types"].get(f) == tag and f in self.raw_headers:
                    prefix = tag.capitalize()
                    required_cols.update(
                        [c if str(c).startswith(f"{prefix}.") else f"{prefix}.{c}" for c in self.raw_headers[f]])

        required_cols = {c for c in required_cols if not any(x in c for x in ['Time', 'Duration', 'DateTime'])}
        missing = required_cols - set(self.gap_rules.keys())
        if missing:
            QMessageBox.information(self, "New Variables Detected",
                                    f"Found {len(missing)} new variables.\nPlease set their missing data treatment before syncing.")
            if not self.open_gap_fill_settings(list(required_cols)):
                self.progress.setValue(0);
                return

        self.progress.setValue(20)
        try:
            self.combined_map, self.summary_map = combinator.run_sync_engine(self.project_data, self.gap_rules,
                                                                             self.tdms_rules)
            if self.combined_map:
                self.btn_export_csvs.setEnabled(True);
                self.progress.setValue(100)
                QMessageBox.information(self, "Success",
                                        f"Data Prepared Successfully! ({len(self.combined_map)} cycles generated in Memory)")
            else:
                self.progress.setValue(0); QMessageBox.warning(self, "Empty", "No valid files paired.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def save_individual_csvs(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Export Folder", get_last_dir("dir_export_csvs"))
        if folder:
            set_last_dir("dir_export_csvs", folder)
            ts = datetime.now().strftime("%m%d%y%H%M%S")
            for name, df in self.combined_map.items():
                df.to_csv(os.path.join(folder, f"{name}_{ts}.csv"), index=False)
            QMessageBox.information(self, "Saved", "Individual Cycle CSVs exported successfully.")

    # --- TAB 2 LOGIC ---
    def on_tab_change(self, i):
        if i == 1: self.rebuild_grid()

    def add_custom_column(self):
        t, ok = QInputDialog.getText(self, "Add Field", "Enter column name:")
        if ok and t:
            self.custom_slots.append(t)
            self.save_grid_settings()
            self.rebuild_grid()

    def remove_custom_column(self):
        if not self.custom_slots: return
        i, ok = QInputDialog.getItem(self, "Remove", "Select column:", self.custom_slots, 0, False)
        if ok and i:
            self.custom_slots.remove(i)
            self.save_grid_settings()
            self.rebuild_grid()

    def import_combined_data(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Combined Data Files", get_last_dir("dir_import_combined"),
                                                "CSV Files (*.csv)")
        if not files: return
        set_last_dir("dir_import_combined", os.path.dirname(files[0]))
        if "imported_combined" not in self.project_data: self.project_data["imported_combined"] = []
        for f in files:
            if f not in self.project_data["imported_combined"]:
                self.project_data["imported_combined"].append(f)
                try:
                    df = dh.load_dataframe(f, nrows=1)
                    if not df.empty:
                        self.raw_headers[f] = list(df.columns)
                        self.project_data["file_types"][f] = "Combined"
                        full_df = dh.load_dataframe(f)
                        cycle_id = os.path.basename(f).replace('.csv', '')
                        if len(cycle_id) > 13 and cycle_id[-13] == '_': cycle_id = cycle_id[:-13]
                        self.combined_map[cycle_id] = full_df
                except:
                    pass
        self.rebuild_grid()
        QMessageBox.information(self, "Imported", "Combined CSV files loaded into memory successfully.")

    def import_summary_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Summary File", get_last_dir("dir_summary"),
                                              "CSV Files (*.csv)")
        if not path: return
        set_last_dir("dir_summary", os.path.dirname(path))
        try:
            df = pd.read_csv(path)
            for _, row in df.iterrows():
                if "Cycle_ID" in row: self.summary_map[row["Cycle_ID"]] = row.to_dict()
            QMessageBox.information(self, "Imported", "Summary memory map restored successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not load summary file: {e}")

    def export_summary_file(self):
        if not self.summary_map:
            QMessageBox.warning(self, "No Summary Data",
                                "No summary data found in memory. Please run 'PREPARE DATA' first.")
            return
        now_str = datetime.now().strftime("%m%d%y%H%M%S")
        default_name = f"summary_{now_str}.csv"
        default_path = os.path.join(get_last_dir("dir_summary"), default_name)
        path, _ = QFileDialog.getSaveFileName(self, "Save Summary File", default_path, "CSV (*.csv)")
        if path:
            set_last_dir("dir_summary", os.path.dirname(path))
            pd.DataFrame(list(self.summary_map.values())).to_csv(path, index=False)
            QMessageBox.information(self, "Saved", "Summary File exported successfully!")

    def export_analyzed_csvs(self):
        if not self.analyzed_map:
            QMessageBox.warning(self, "No Analyzed Data",
                                "No analyzed data found in memory. Please run 'ANALYZE DATA' first.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Select Export Folder", get_last_dir("dir_export_csvs"))
        if folder:
            set_last_dir("dir_export_csvs", folder)
            ts = datetime.now().strftime("%m%d%y%H%M%S")
            for name, df in self.analyzed_map.items():
                export_name = name.replace("_Combined", "_Analyzed")
                if not export_name.endswith("_Analyzed"): export_name += "_Analyzed"
                df.to_csv(os.path.join(folder, f"{export_name}_{ts}.csv"), index=False)
            QMessageBox.information(self, "Saved", "Analyzed Cycle CSVs exported successfully.")

    def execute_analyzer(self):
        self.progress.setValue(20)
        analyzed_count = 0
        script_errors = []
        try:
            alpha = float(self.project_data.get("alpha_filter", 0.95))
            script_path = self.project_data.get("custom_script", "")

            for cycle_id, mapping in self.project_data["individual_mappings"].items():
                if cycle_id in self.combined_map:
                    df_in = self.combined_map[cycle_id]
                    sum_row = self.summary_map.get(cycle_id, {"Cycle_ID": cycle_id})

                    resolved_map = {}
                    master = self.project_data["master_template"]

                    for slot in self.config_slots + self.ts_slots + self.custom_slots:
                        val = mapping.get(slot, "")
                        if val == "" or val == "-- Use Master --":
                            resolved_map[slot] = master.get(slot, "")
                        else:
                            resolved_map[slot] = val

                    for slot in self.single_slots:
                        src = mapping.get(slot + "_src", "-- Use Master --")
                        if src == "" or src == "-- Use Master --":
                            resolved_map[slot + "_src"] = master.get(slot + "_src", "Tuning Book")
                            resolved_map[slot + "_val"] = master.get(slot + "_val", "")
                            resolved_map[slot + "_unit"] = master.get(slot + "_unit", "lb")
                        else:
                            resolved_map[slot + "_src"] = src
                            resolved_map[slot + "_val"] = mapping.get(slot + "_val", "")
                            resolved_map[slot + "_unit"] = mapping.get(slot + "_unit", "lb")

                    for slot in ["Power_Phase_1", "Power_Phase_2"]:
                        unit = mapping.get(slot + "_unit", "-- Use Master --")
                        if unit == "" or unit == "-- Use Master --":
                            resolved_map[slot + "_unit"] = master.get(slot + "_unit", "W")
                        else:
                            resolved_map[slot + "_unit"] = unit

                    fuel_type = "E"
                    for p in self.project_data["pairs"]:
                        if p.get("cycle") + "_Combined" == cycle_id:
                            fuel_type = p.get("fuel_type", "E")
                            break
                    resolved_map["Fuel_Type"] = fuel_type
                    resolved_map["custom_slots"] = self.custom_slots

                    df_out, sum_row_out = analyzer.run_analysis(df_in, resolved_map, sum_row, alpha, script_path)

                    if "Custom_Script_Error" in sum_row_out:
                        script_errors.append(f"• {cycle_id}: {sum_row_out['Custom_Script_Error']}")

                    self.analyzed_map[cycle_id] = df_out
                    self.summary_map[cycle_id] = sum_row_out
                    analyzed_count += 1

            if analyzed_count > 0:
                self.progress.setValue(100)
                if script_errors:
                    err_msg = "Data Analyzed & Trimmed Successfully!\n\nHOWEVER, your Custom Script failed on these cycles:\n\n" + "\n".join(
                        script_errors) + "\n\nPlease check your script variables."
                    QMessageBox.warning(self, "Analysis Completed with Warnings", err_msg)
                else:
                    QMessageBox.information(self, "Success",
                                            f"Data Analyzed & Trimmed Successfully! ({analyzed_count} cycles calculated)")
            else:
                self.progress.setValue(0)
                QMessageBox.warning(self, "Warning",
                                    "No mapped combined cycles found in memory. Did you prepare or import them?")
        except Exception as e:
            QMessageBox.critical(self, "Error in Analytics Engine", str(e))

    def remove_grid_row(self, src_type, item):
        if src_type == "pair" and item in self.project_data["pairs"]:
            self.project_data["pairs"].remove(item)
            self.refresh_pairing_table()
        elif src_type == "file" and item in self.project_data["files"]:
            self.remove_file(item)
        elif src_type == "imported" and item in self.project_data.get("imported_combined", []):
            self.project_data["imported_combined"].remove(item)
            if item in self.raw_headers: del self.raw_headers[item]
            cycle_id = os.path.basename(item).replace('.csv', '')
            if len(cycle_id) > 13 and cycle_id[-13] == '_': cycle_id = cycle_id[:-13]
            if cycle_id in self.combined_map: del self.combined_map[cycle_id]
        self.rebuild_grid()

    def toggle_rmc_inputs(self, val, w1, w2):
        if val == "-- Use Master --": val = self.project_data["master_template"].get("Estimated_RMC_Method", "")
        if "1. Use Adjusted_Weight_Method" in val:
            w1.setEnabled(False);
            w2.setEnabled(False)
        else:
            w1.setEnabled(True);
            w2.setEnabled(True)

    def on_grid_row_clicked(self, row, col):
        for r in range(self.grid.rowCount()):
            for c in range(self.grid.columnCount()):
                item = self.grid.item(r, c)
                if item: item.setBackground(QColor("#5c4033") if r == 0 else QColor("#1e1e1e"))

        if row == 0: return

        for c in range(self.grid.columnCount()):
            item = self.grid.item(row, c)
            if item: item.setBackground(QColor("#3d3d3d"))

        mapping_key = self.grid.item(row, 1).text()
        if mapping_key in self.combined_map:
            df = self.combined_map[mapping_key].head(50)
            self.monitor_group.setTitle(f"🔍 Data Monitor - {mapping_key} (First 50 Rows)")
            self.monitor_table.clear()
            self.monitor_table.setColumnCount(len(df.columns))
            self.monitor_table.setRowCount(len(df.index))
            self.monitor_table.setHorizontalHeaderLabels([str(c) for c in df.columns])

            for r_idx in range(len(df.index)):
                for c_idx, col_name in enumerate(df.columns):
                    val = str(df.iloc[r_idx, c_idx])
                    self.monitor_table.setItem(r_idx, c_idx, QTableWidgetItem(val))
            self.monitor_table.resizeColumnsToContents()
        else:
            self.monitor_table.clear()
            self.monitor_table.setRowCount(0)
            self.monitor_table.setColumnCount(0)
            self.monitor_group.setTitle("🔍 Data Monitor")

    def rebuild_grid(self):
        h_scroll = self.grid.horizontalScrollBar().value()
        v_scroll = self.grid.verticalScrollBar().value()

        self.grid.clear();
        all_slots = self.config_slots + self.single_slots + self.ts_slots + self.custom_slots
        self.grid.setColumnCount(len(all_slots) + 2)
        self.grid.setHorizontalHeaderLabels(["", "Cycle"] + all_slots)

        rows = []
        mode = self.project_data["analysis_mode"]
        if "Merge" in mode:
            for p in self.project_data["pairs"]:
                if p.get("daq") or p.get("mac"):
                    cycle_id = p.get("cycle") + "_Combined"
                    rows.append(("pair", p, cycle_id, cycle_id))
        else:
            tag = "DAQ" if "DAQ" in mode else "MAC"
            for f in self.project_data["files"]:
                if self.project_data["file_types"].get(f) == tag:
                    cycle_id = os.path.basename(f).replace('.csv', '') + "_Combined"
                    rows.append(("file", f, cycle_id, cycle_id))

        for f in self.project_data.get("imported_combined", []):
            cycle_id = os.path.basename(f).replace('.csv', '')
            if len(cycle_id) > 13 and cycle_id[-13] == '_': cycle_id = cycle_id[:-13]
            rows.append(("imported", f, cycle_id, cycle_id))

        self.grid.setRowCount(len(rows) + 1)

        all_heads = set()
        for f, h in self.raw_headers.items():
            pre = self.project_data["file_types"].get(f, "Raw").capitalize()
            for col in h:
                if pre == "Combined":
                    all_heads.add(str(col))
                elif str(col).startswith(f"{pre}."):
                    all_heads.add(col)
                else:
                    all_heads.add(f"{pre}.{col}")
        for c in self.tuning_df.columns: all_heads.add(f"Tun.{c}")
        all_heads = sorted(list(all_heads)) + ["Common_Time_Sec"]

        master_label = QTableWidgetItem("MASTER TEMPLATE")
        master_label.setBackground(QBrush(QColor("#5c4033")));
        master_label.setForeground(QBrush(QColor("white")))

        self.grid.setCellWidget(0, 0, QWidget())
        self.grid.setItem(0, 1, master_label)

        for ci, s in enumerate(all_slots):
            if s == "Scale_Weight_Method":
                cb = QComboBox();
                cb.addItems(self.weight_opts);
                cb.setCurrentText(self.project_data["master_template"].get(s, self.weight_opts[0]))
                cb.currentTextChanged.connect(lambda t, sl=s: self.up_master(sl, t));
                self.grid.setCellWidget(0, ci + 2, cb)
            elif s == "Adjusted_Weight_Method":
                cb = QComboBox();
                cb.addItems(self.adjust_opts);
                cb.setCurrentText(self.project_data["master_template"].get(s, self.adjust_opts[0]))
                cb.currentTextChanged.connect(lambda t, sl=s: self.up_master(sl, t));
                self.grid.setCellWidget(0, ci + 2, cb)
            elif s == "Estimated_RMC_Method":
                cb = QComboBox();
                cb.addItems(self.rmc_opts);
                cb.setCurrentText(self.project_data["master_template"].get(s, self.rmc_opts[0]))
                cb.currentTextChanged.connect(lambda t, sl=s: self.up_master(sl, t));
                self.grid.setCellWidget(0, ci + 2, cb)
                self.master_rmc_cb = cb
            elif s in self.single_slots:
                container = QWidget();
                lay = QHBoxLayout(container);
                lay.setContentsMargins(0, 0, 0, 0)

                src_opts = ["Tuning Book", "Manual"]
                if s == "Start_Weight": src_opts.append("Use First Scale Weight")
                if s == "End_Weight": src_opts.append("Use Last Scale Weight")

                cb_src = QComboBox();
                cb_src.addItems(src_opts)
                current_src = self.project_data["master_template"].get(s + "_src", "Tuning Book")
                cb_src.setCurrentText(current_src)

                val_input = QLineEdit()
                val_input.setText(self.project_data["master_template"].get(s + "_val", ""))
                val_input.setEnabled(current_src == "Manual")

                def on_m_src(t, sl=s, inp=val_input):
                    inp.setEnabled(t == "Manual")
                    self.up_master(sl + "_src", t)

                cb_src.currentTextChanged.connect(on_m_src)
                val_input.textChanged.connect(lambda t, sl=s: self.up_master(sl + "_val", t))

                lay.addWidget(cb_src);
                lay.addWidget(val_input);

                # --- NEW CONDITIONAL WRAPPER ---
                if "RMC" not in s:
                    cb_unit = QComboBox();
                    cb_unit.addItems(["lb", "kg"])
                    cb_unit.setCurrentText(self.project_data["master_template"].get(s + "_unit", "lb"))
                    cb_unit.setFixedWidth(50)
                    cb_unit.currentTextChanged.connect(lambda t, sl=s: self.up_master(sl + "_unit", t))
                    lay.addWidget(cb_unit)

                self.grid.setCellWidget(0, ci + 2, container)

            elif s in ["Power_Phase_1", "Power_Phase_2"]:
                container = QWidget();
                lay = QHBoxLayout(container);
                lay.setContentsMargins(0, 0, 0, 0)
                cb = create_searchable_cb(["-- Template --"] + all_heads)
                cb.setCurrentText(self.project_data["master_template"].get(s, ""))

                cb_unit = QComboBox();
                cb_unit.addItems(["W", "kW"])
                cb_unit.setCurrentText(self.project_data["master_template"].get(s + "_unit", "W"))
                cb_unit.setFixedWidth(50)

                cb.currentTextChanged.connect(lambda t, sl=s: self.up_master(sl, t))
                cb_unit.currentTextChanged.connect(lambda t, sl=s: self.up_master(sl + "_unit", t))

                lay.addWidget(cb);
                lay.addWidget(cb_unit)
                self.grid.setCellWidget(0, ci + 2, container)
            else:
                if s in ["Inlet_Vaisala_MR", "Exhaust_Vaisala_MR"]:
                    cb = create_searchable_cb(["-- Template --", "Calculate from Temp/RH"] + all_heads)
                else:
                    cb = create_searchable_cb(["-- Template --"] + all_heads)
                cb.setCurrentText(self.project_data["master_template"].get(s, ""))
                cb.currentTextChanged.connect(lambda t, sl=s: self.up_master(sl, t));
                self.grid.setCellWidget(0, ci + 2, cb)

        for ri, (src_type, item_ref, d_name, mapping_key) in enumerate(rows, start=1):
            if mapping_key not in self.project_data["individual_mappings"]:
                self.project_data["individual_mappings"][mapping_key] = {}

            btn_remove = QPushButton("❌")
            btn_remove.setStyleSheet("color: red; border: none; background: transparent;")
            btn_remove.clicked.connect(lambda _, s=src_type, i=item_ref: self.remove_grid_row(s, i))
            self.grid.setCellWidget(ri, 0, btn_remove)

            item_label = QTableWidgetItem(d_name)
            item_label.setBackground(QBrush(QColor("#1e1e1e")))
            self.grid.setItem(ri, 1, item_label)

            rmc_opt_widget = None
            start_rmc_widget = None
            end_rmc_widget = None

            cycle_headers = []
            if mapping_key in self.combined_map: cycle_headers = list(self.combined_map[mapping_key].columns)
            for c in self.tuning_df.columns: cycle_headers.append(f"Tun.{c}")
            cycle_headers = sorted(list(set(cycle_headers))) + ["Common_Time_Sec"]

            for ci, s in enumerate(all_slots):
                saved_val = self.project_data["individual_mappings"][mapping_key].get(s, "")

                if s == "Scale_Weight_Method":
                    cb = QComboBox();
                    cb.addItems(["-- Use Master --"] + self.weight_opts)
                    if saved_val: cb.setCurrentText(saved_val)
                    cb.currentTextChanged.connect(lambda t, mk=mapping_key, sl=s: self.save_indiv(mk, sl, t))
                    self.grid.setCellWidget(ri, ci + 2, cb)

                elif s == "Adjusted_Weight_Method":
                    cb = QComboBox();
                    cb.addItems(["-- Use Master --"] + self.adjust_opts)
                    if saved_val: cb.setCurrentText(saved_val)
                    cb.currentTextChanged.connect(lambda t, mk=mapping_key, sl=s: self.save_indiv(mk, sl, t))
                    self.grid.setCellWidget(ri, ci + 2, cb)

                elif s == "Estimated_RMC_Method":
                    cb = QComboBox();
                    cb.addItems(["-- Use Master --"] + self.rmc_opts)
                    if saved_val: cb.setCurrentText(saved_val)
                    cb.currentTextChanged.connect(lambda t, mk=mapping_key, sl=s: self.save_indiv(mk, sl, t))
                    rmc_opt_widget = cb
                    self.grid.setCellWidget(ri, ci + 2, cb)


                elif s in self.single_slots:

                    container = QWidget();

                    lay = QHBoxLayout(container);

                    lay.setContentsMargins(0, 0, 0, 0)

                    src_opts = ["-- Use Master --", "Tuning Book", "Manual"]

                    if s == "Start_Weight": src_opts.append("Use First Scale Weight")

                    if s == "End_Weight": src_opts.append("Use Last Scale Weight")

                    cb_src = QComboBox();

                    cb_src.addItems(src_opts)

                    saved_src = self.project_data["individual_mappings"][mapping_key].get(s + "_src",
                                                                                          "-- Use Master --")

                    cb_src.setCurrentText(saved_src)

                    val_input = QLineEdit()

                    master_val = self.project_data["master_template"].get(s + "_val", "")

                    val_input.setPlaceholderText(f"M: {master_val}" if saved_src == "-- Use Master --" else "Value/Col")

                    saved_val_input = self.project_data["individual_mappings"][mapping_key].get(s + "_val", "")

                    val_input.setText(saved_val_input)

                    val_input.setEnabled(saved_src == "Manual")

                    def on_src_change(t, mk=mapping_key, sl=s, inp=val_input):

                        self.save_indiv(mk, sl + "_src", t)

                        inp.setEnabled(t == "Manual")

                        m_val = self.project_data["master_template"].get(sl + "_val", "")

                        inp.setPlaceholderText(f"M: {m_val}" if t == "-- Use Master --" else "Value/Col")

                    cb_src.currentTextChanged.connect(on_src_change)

                    val_input.textChanged.connect(lambda t, mk=mapping_key, sl=s: self.save_indiv(mk, sl + "_val", t))

                    lay.addWidget(cb_src);

                    lay.addWidget(val_input);

                    # --- NEW CONDITIONAL WRAPPER ---

                    if "RMC" not in s:
                        cb_unit = QComboBox();

                        cb_unit.addItems(["-- Use Master --", "lb", "kg"])

                        cb_unit.setCurrentText(
                            self.project_data["individual_mappings"][mapping_key].get(s + "_unit", "-- Use Master --"))

                        cb_unit.setFixedWidth(50)

                        cb_unit.currentTextChanged.connect(
                            lambda t, mk=mapping_key, sl=s: self.save_indiv(mk, sl + "_unit", t))

                        lay.addWidget(cb_unit)

                    if s == "Start_RMC": start_rmc_widget = container

                    if s == "End_RMC": end_rmc_widget = container

                    self.grid.setCellWidget(ri, ci + 2, container)

                else:
                    container = QWidget();
                    lay = QHBoxLayout(container);
                    lay.setContentsMargins(0, 0, 0, 0)

                    if s in ["Inlet_Vaisala_MR", "Exhaust_Vaisala_MR"]:
                        opts = ["-- Use Master --", "Calculate from Temp/RH"] + cycle_headers
                    else:
                        opts = ["-- Use Master --"] + cycle_headers

                    cb = create_searchable_cb(opts)

                    val_to_check = saved_val if saved_val and saved_val != "-- Use Master --" else self.project_data[
                        "master_template"].get(s, "")
                    if val_to_check and val_to_check not in cycle_headers and val_to_check not in ["",
                                                                                                   "-- Use Master --",
                                                                                                   "Calculate from Temp/RH"]:
                        cb.setStyleSheet("background-color: #8B0000; color: white;")
                        cb.addItem(val_to_check)

                    if saved_val:
                        cb.setCurrentText(saved_val)
                    else:
                        m_val = self.project_data["master_template"].get(s, "")
                        if m_val in opts or m_val == "Calculate from Temp/RH" or m_val == "": cb.setCurrentText(m_val)

                    cb.currentTextChanged.connect(lambda t, mk=mapping_key, sl=s: self.save_indiv(mk, sl, t))
                    lay.addWidget(cb)

                    if s in ["Power_Phase_1", "Power_Phase_2"]:
                        cb_unit = QComboBox();
                        cb_unit.addItems(["-- Use Master --", "W", "kW"])
                        cb_unit.setCurrentText(
                            self.project_data["individual_mappings"][mapping_key].get(s + "_unit", "-- Use Master --"))
                        cb_unit.currentTextChanged.connect(
                            lambda t, mk=mapping_key, sl=s: self.save_indiv(mk, sl + "_unit", t))
                        cb_unit.setFixedWidth(50)
                        lay.addWidget(cb_unit)

                    self.grid.setCellWidget(ri, ci + 2, container)

            if rmc_opt_widget and start_rmc_widget and end_rmc_widget:
                self.toggle_rmc_inputs(rmc_opt_widget.currentText(), start_rmc_widget, end_rmc_widget)
                rmc_opt_widget.currentTextChanged.connect(
                    lambda val, w1=start_rmc_widget, w2=end_rmc_widget: self.toggle_rmc_inputs(val, w1, w2))
                self.master_rmc_cb.currentTextChanged.connect(
                    lambda _, val_box=rmc_opt_widget, w1=start_rmc_widget, w2=end_rmc_widget: self.toggle_rmc_inputs(
                        val_box.currentText(), w1, w2))

        self.grid.resizeColumnsToContents()
        for c in range(self.grid.columnCount()):
            if self.grid.columnWidth(c) > 400:
                self.grid.setColumnWidth(c, 400)
        self.grid.setColumnWidth(1, 180)

        self.grid.horizontalScrollBar().setValue(h_scroll)
        self.grid.verticalScrollBar().setValue(v_scroll)

    def up_master(self, s, t):
        self.project_data["master_template"][s] = t
        self.save_grid_settings()
        self.rebuild_grid()

    def save_indiv(self, mapping_key, slot, val):
        self.project_data["individual_mappings"][mapping_key][slot] = val

    def save_project_file(self, p, silent=False, is_autosave=False):
        data = {
            "files": self.project_data["files"],
            "file_types": self.project_data["file_types"],
            "pairs": self.project_data["pairs"],
            "analysis_mode": self.mode_combo.currentText(),
            "master_template": self.project_data["master_template"],
            "individual_mappings": self.project_data["individual_mappings"],
            "raw_headers": self.raw_headers,
            "imported_combined": self.project_data.get("imported_combined", []),
            "alpha_filter": self.project_data.get("alpha_filter", "0.95"),
            "custom_script": self.project_data.get("custom_script", "")
        }
        with open(p, 'w') as f:
            json.dump(data, f)
        if not is_autosave:
            set_last_dir("last_saved_project_file", p)
        if not silent: QMessageBox.information(self, "Saved", "Project saved successfully.")

    def save_project(self, silent=False):
        if silent:
            path = get_last_dir("last_saved_project_file")
            if path and os.path.exists(path):
                self.save_project_file(path, silent=True)
        else:
            p, _ = QFileDialog.getSaveFileName(self, "Save Project", get_last_dir("dir_project"), "UDA Project (*.uda)")
            if p:
                set_last_dir("dir_project", os.path.dirname(p))
                self.save_project_file(p, silent=False)

    def load_project_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "Load Project", get_last_dir("dir_project"),
                                           "UDA Project (*.uda);;JSON Project (*.json)")
        if p:
            set_last_dir("dir_project", os.path.dirname(p))
            self.load_project_file(p, silent=False)

    def load_project_file(self, p, silent=False):
        try:
            with open(p, 'r') as f:
                data = json.load(f)

            try:
                loaded_files = data.get("files", [])
                valid_files = [f for f in loaded_files if os.path.exists(f)]
                self.project_data["files"] = valid_files
                self.project_data["file_types"] = {k: v for k, v in data.get("file_types", {}).items() if
                                                   k in valid_files}

                valid_pairs = []
                for pair in data.get("pairs", []):
                    if pair.get("daq") and not os.path.exists(pair.get("daq")): pair["daq"] = ""
                    if pair.get("mac") and not os.path.exists(pair.get("mac")): pair["mac"] = ""
                    valid_pairs.append(pair)
                self.project_data["pairs"] = valid_pairs
            except:
                pass

            self.mode_combo.setCurrentText(data.get("analysis_mode", "Merge DAQ + MAC"))
            self.project_data["master_template"] = data.get("master_template", {})
            self.project_data["individual_mappings"] = data.get("individual_mappings", {})

            try:
                self.raw_headers = data.get("raw_headers", {})
                for f_key, h_list in self.raw_headers.items():
                    if f_key in self.project_data["files"]:
                        self.raw_headers[f_key] = [re.sub(r'[^\x00-\x7F]+', '', str(c)).strip() for c in h_list]
            except:
                pass

            self.project_data["imported_combined"] = data.get("imported_combined", [])
            self.project_data["alpha_filter"] = data.get("alpha_filter", "0.95")
            self.alpha_input.setText(self.project_data["alpha_filter"])
            self.project_data["custom_script"] = data.get("custom_script", "")
            if self.project_data["custom_script"]:
                self.btn_custom_script.setText(f"Script: {os.path.basename(self.project_data['custom_script'])}")

            if not p.endswith(".autosave.uda"):
                set_last_dir("last_saved_project_file", p)

            self.apply_default_master_template()
            self.rebuild_file_table()
            self.refresh_pairing_table()
            if not silent: QMessageBox.information(self, "Loaded", "Project loaded successfully.")
        except Exception as e:
            if not silent: QMessageBox.critical(self, "Error", f"Failed to load project: {str(e)}")


if __name__ == "__main__":
    app = QApplication(sys.argv);
    ex = UniversalAnalyzer();
    ex.show();
    sys.exit(app.exec())