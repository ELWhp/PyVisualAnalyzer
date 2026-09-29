import sys, os, json
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                               QLabel, QComboBox, QTreeWidget, QTreeWidgetItem,
                               QTabWidget, QWidget, QTableWidget, QTableWidgetItem,
                               QFileDialog, QGroupBox, QHeaderView, QMessageBox)


class TDMSHarmonizerDialog(QDialog):
    def __init__(self, parent=None, sample_file=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ TDMS Harmonizer Rules")
        self.resize(900, 700)

        self.setStyleSheet("""
            QDialog { background-color: #2b2b2b; }
            QLabel, QGroupBox { color: white; font-weight: bold; }
            QTreeWidget, QTableWidget { background-color: #1e1e1e; color: white; alternate-background-color: #2a2a2a; }
            QHeaderView::section { background-color: #333333; color: white; font-weight: bold; padding: 4px; }
            QComboBox, QLineEdit { background-color: #333333; color: white; border: 1px solid #555; }
            QComboBox QAbstractItemView { background-color: #333333; color: white; }
            QPushButton { color: white; font-weight: bold; border-radius: 4px; padding: 5px; }
        """)

        self.rules_file = "tdms_rules.json"
        self.rules = {"data_group": "DAQ", "name_path": "", "unit_path": "", "manual_overrides": {}}
        self.load_rules()
        self.init_ui()
        if sample_file: self.load_sample_tdms(sample_file)

    def init_ui(self):
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()

        tab_auto = QWidget();
        auto_lay = QVBoxLayout(tab_auto)
        info = QLabel(
            "The Auto-Harmonizer checks 'Hardware Config/Channel Name' by default.\nIf your file uses different folders for variable names, map them below:")
        auto_lay.addWidget(info)

        top_bar = QHBoxLayout()
        btn_load_tdms = QPushButton("📂 Load Sample TDMS File to Inspect")
        btn_load_tdms.setStyleSheet("background-color: #3498db;")
        btn_load_tdms.clicked.connect(lambda: self.load_sample_tdms())
        self.lbl_sample = QLabel("No sample loaded.")
        top_bar.addWidget(btn_load_tdms);
        top_bar.addWidget(self.lbl_sample);
        top_bar.addStretch()
        auto_lay.addLayout(top_bar)

        tree_group = QGroupBox("TDMS Internal Directory Explorer")
        tree_lay = QVBoxLayout(tree_group)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Group / Channel", "Properties Count"])
        tree_lay.addWidget(self.tree)
        auto_lay.addWidget(tree_group)

        path_group = QGroupBox("Directory Mapping Paths")
        path_lay = QVBoxLayout(path_group)
        row1 = QHBoxLayout();
        row1.addWidget(QLabel("<b>1. Mathematical Data Group:</b>"))
        self.cb_data_group = QComboBox();
        self.cb_data_group.setEditable(True);
        row1.addWidget(self.cb_data_group)
        row2 = QHBoxLayout();
        row2.addWidget(QLabel("<b>2. Variable Names Directory:</b>"))
        self.cb_name_path = QComboBox();
        self.cb_name_path.setEditable(True);
        row2.addWidget(self.cb_name_path)
        path_lay.addLayout(row1);
        path_lay.addLayout(row2)
        auto_lay.addWidget(path_group)

        tab_manual = QWidget();
        man_lay = QVBoxLayout(tab_manual)
        btn_add_override = QPushButton("➕ Add Override Rule")
        btn_add_override.setStyleSheet("background-color: #e67e22;")
        btn_add_override.clicked.connect(lambda: self.add_override_row())
        man_lay.addWidget(btn_add_override)

        self.override_table = QTableWidget(0, 3)
        self.override_table.setHorizontalHeaderLabels(
            ["", "Raw TDMS Name (e.g., 'P1 Volt LL')", "Harmonized Name (e.g., 'P1 Volt LL (V)')"])
        self.override_table.setColumnWidth(0, 40)
        self.override_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.override_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        man_lay.addWidget(self.override_table)

        self.tabs.addTab(tab_auto, "1. Directory Pathing")
        self.tabs.addTab(tab_manual, "2. Manual Overrides (Failsafe)")
        layout.addWidget(self.tabs)

        bot_bar = QHBoxLayout()
        btn_save = QPushButton("💾 Save Harmonizer Rules")
        btn_save.setStyleSheet("background-color: #2ecc71; height: 40px;")
        btn_save.clicked.connect(self.save_rules)
        bot_bar.addStretch();
        bot_bar.addWidget(btn_save)
        layout.addLayout(bot_bar)

        self.populate_ui_from_rules()

    def remove_override_row(self):
        btn = self.sender()
        if btn:
            idx = self.override_table.indexAt(btn.pos())
            if idx.isValid(): self.override_table.removeRow(idx.row())

    def load_sample_tdms(self, path=None):
        if not path: path, _ = QFileDialog.getOpenFileName(self, "Select Sample TDMS", "", "TDMS Files (*.tdms)")
        if not path: return
        self.lbl_sample.setText(os.path.basename(path))
        self.tree.clear();
        self.cb_data_group.clear();
        self.cb_name_path.clear()

        try:
            from nptdms import TdmsFile
            tdms = TdmsFile.read(path)
            paths = []
            for group in tdms.groups():
                g_node = QTreeWidgetItem([group.name, ""])
                self.tree.addTopLevelItem(g_node)
                self.cb_data_group.addItem(group.name)
                for channel in group.channels()[:50]:
                    props_count = str(len(channel.properties)) if channel.properties else "0"
                    c_node = QTreeWidgetItem([channel.name, f"{props_count} props"])
                    g_node.addChild(c_node)
                    paths.append(f"{group.name}/{channel.name}")

            self.cb_name_path.addItems(["-- Select Path --"] + paths)
            self.tree.expandAll();
            self.populate_ui_from_rules()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to read TDMS:\n{e}")

    def add_override_row(self, orig="", new=""):
        row = self.override_table.rowCount()
        self.override_table.insertRow(row)
        btn_del = QPushButton("❌")
        btn_del.setStyleSheet("color: red; border: none; background: transparent;")
        btn_del.clicked.connect(self.remove_override_row)
        self.override_table.setCellWidget(row, 0, btn_del)
        self.override_table.setItem(row, 1, QTableWidgetItem(orig))
        self.override_table.setItem(row, 2, QTableWidgetItem(new))

    def load_rules(self):
        if os.path.exists(self.rules_file):
            try:
                with open(self.rules_file, 'r') as f:
                    self.rules.update(json.load(f))
            except:
                pass

    def populate_ui_from_rules(self):
        self.cb_data_group.setCurrentText(self.rules.get("data_group", "DAQ"))
        self.cb_name_path.setCurrentText(self.rules.get("name_path", ""))
        self.override_table.setRowCount(0)
        for k, v in self.rules.get("manual_overrides", {}).items(): self.add_override_row(k, v)

    def sanitize_string(self, s):
        return "".join(c for c in s if ord(c) < 128)

    def save_rules(self):
        self.rules["data_group"] = self.cb_data_group.currentText()
        self.rules["name_path"] = self.cb_name_path.currentText()
        overrides = {}
        for r in range(self.override_table.rowCount()):
            orig_item = self.override_table.item(r, 1)
            new_item = self.override_table.item(r, 2)
            if orig_item and new_item:
                orig = self.sanitize_string(orig_item.text().strip())
                new = self.sanitize_string(new_item.text().strip())
                if orig: overrides[orig] = new
        self.rules["manual_overrides"] = overrides
        with open(self.rules_file, 'w') as f:
            json.dump(self.rules, f, indent=4)
        QMessageBox.information(self, "Saved", "Harmonizer Rules saved successfully.")
        self.accept()