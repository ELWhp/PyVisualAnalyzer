import os, zipfile, io, re
import pandas as pd
import numpy as np


def sanitize_csv(file_content_bytes):
    return file_content_bytes.decode('ascii', errors='ignore')


def clean_headers(df):
    """Absolute Firewall: Destroys any non-ASCII character (like °, 蚓, ÿ) from headers."""
    df.columns = [re.sub(r'[^\x00-\x7F]+', '', str(c)).strip() for c in df.columns]
    return df


def get_separator(text):
    """Smart detector: checks if the file uses Tabs or Commas."""
    first_line = text.split('\n', 1)[0] if '\n' in text else text
    return '\t' if '\t' in first_line else ','


def load_dataframe(path, nrows=None, tdms_rules=None):
    try:
        if path.lower().endswith('.mac') or zipfile.is_zipfile(path):
            with zipfile.ZipFile(path, 'r') as z:
                acq_file = next((f for f in z.namelist() if f.endswith('Acquisition.txt')), None)
                if acq_file:
                    with z.open(acq_file) as f:
                        clean_data = sanitize_csv(f.read())
                        sep = get_separator(clean_data)
                        df = pd.read_csv(io.StringIO(clean_data), sep=sep, nrows=nrows, on_bad_lines='skip',
                                         low_memory=False).fillna(-1)
                        return clean_headers(df)
                return pd.DataFrame()

        elif path.lower().endswith('.tdms'):
            from nptdms import TdmsFile
            tdms = TdmsFile.read(path)
            df = tdms.as_dataframe()
            if nrows: df = df.head(nrows)

            tdms_rules = tdms_rules or {}
            manual_overrides = tdms_rules.get("manual_overrides", {})
            name_path = tdms_rules.get("name_path", "")

            new_cols = []
            for c in df.columns:
                if not isinstance(c, str):
                    new_cols.append(c)
                    continue

                clean_name = c.split('/')[-1].replace("'", "")

                if clean_name in manual_overrides:
                    new_cols.append(manual_overrides[clean_name])
                    continue

                mapped_name = None
                if name_path and '/' in name_path:
                    try:
                        g, ch = name_path.split('/', 1)
                        names_array = tdms[g][ch][:]
                        matches = [str(n) for n in names_array if clean_name in str(n)]
                        if matches: mapped_name = matches[0]
                    except:
                        pass
                if mapped_name:
                    new_cols.append(mapped_name)
                    continue

                auto_name = None
                try:
                    if 'Hardware Config' in tdms and 'Channel Name' in tdms['Hardware Config']:
                        names_array = tdms['Hardware Config']['Channel Name'][:]
                        matches = [str(n) for n in names_array if clean_name in str(n)]
                        if matches: auto_name = matches[0]
                except:
                    pass

                new_cols.append(auto_name if auto_name else clean_name)
            df.columns = new_cols
            return clean_headers(df)

        else:
            with open(path, 'rb') as f:
                clean_data = sanitize_csv(f.read())
                sep = get_separator(clean_data)
                df = pd.read_csv(io.StringIO(clean_data), sep=sep, nrows=nrows, on_bad_lines='skip',
                                 low_memory=False).fillna(-1)
                return clean_headers(df)

    except Exception as e:
        print(f"Load Error on {path}: {e}")
        return pd.DataFrame()


def check_tdms_needs_harmonization(path, tdms_rules=None):
    df = load_dataframe(path, nrows=1, tdms_rules=tdms_rules)
    cols_with_units = [c for c in df.columns if '(' in c and ')' in c]
    return len(df.columns) > 5 and len(cols_with_units) < 2


def extract_mac_metadata(f_path):
    info = {"Appliance": "", "Model": "", "Serial": "", "FW Release": ""}
    try:
        if f_path.lower().endswith('.mac') or zipfile.is_zipfile(f_path):
            with zipfile.ZipFile(f_path, 'r') as z:
                ctx = next((x for x in z.namelist() if "Information Context" in x), None)
                if ctx:
                    raw = sanitize_csv(z.read(ctx))
                    a = re.search(r"Appliance\s*([^\r\n]*)", raw)
                    m = re.search(r"Model Number:\s*([^\r\n]*)", raw)
                    s = re.search(r"Serial Number:\s*([^\r\n]*)", raw)
                    v = re.search(r"Release Number:\s*([^\r\n]*)", raw)

                    if a: info["Appliance"] = a.group(1).replace('ÿ', '').strip()
                    if m: info["Model"] = m.group(1).replace('ÿ', '').strip()
                    if s: info["Serial"] = s.group(1).replace('ÿ', '').strip()
                    if v: info["FW Release"] = v.group(1).replace('ÿ', '').strip()
    except Exception:
        pass
    return info


def extract_daq_metadata(f_path):
    info = {"DAQ_Station": "", "DAQ_Device": ""}
    try:
        if f_path.lower().endswith('.tdms'):
            from nptdms import TdmsFile
            tdms = TdmsFile.read(f_path)
            if 'name' in tdms.properties:
                info["DAQ_Station"] = str(tdms.properties['name'])
            if 'Hardware Config' in tdms and 'Device ID' in tdms['Hardware Config']:
                dev_array = tdms['Hardware Config']['Device ID'][:]
                if len(dev_array) > 0:
                    info["DAQ_Device"] = str(dev_array[0])
    except Exception:
        pass
    return info


def get_time_col(df):
    time_names = ['run time (sec)', 'duration', 'time_elapsed', 'time', 'datetime', 'time (sec)', 'time (s)']
    for c in df.columns:
        if str(c).strip().lower() in time_names: return c
    return None


def clean_data(df):
    if df.columns.duplicated().any():
        df.columns = [f"{c}_{i}" if d else c for i, (c, d) in enumerate(zip(df.columns, df.columns.duplicated()))]
    num = df.select_dtypes(include=[np.number])
    mask = (num.notna()) & (num != 0)
    if not mask.empty and mask.any(axis=1).any():
        return df.loc[mask.any(axis=1).idxmax():].reset_index(drop=True)
    return df