import pandas as pd
import numpy as np
import os
import re
import data_handler as dh


def get_absolute_time_series(df):
    """Detects real-world calendar time to perfectly align asynchronous files."""
    if 'Date' in df.columns and 'Time' in df.columns:
        try:
            dt_str = df['Date'].astype(str).str.strip() + ' ' + df['Time'].astype(str).str.strip()
            dt_series = pd.to_datetime(dt_str, format='mixed', errors='coerce')
            if getattr(dt_series.dt, 'tz', None) is not None: dt_series = dt_series.dt.tz_convert(None)
            if dt_series.notna().any():
                return (dt_series - pd.Timestamp("1970-01-01")).dt.total_seconds()
        except:
            pass

    if 'DateTime' in df.columns:
        try:
            dt_series = pd.to_datetime(df['DateTime'], format='mixed', errors='coerce')
            if getattr(dt_series.dt, 'tz', None) is not None: dt_series = dt_series.dt.tz_convert(None)
            if dt_series.notna().any():
                return (dt_series - pd.Timestamp("1970-01-01")).dt.total_seconds()
        except:
            pass

    return None


def process_file_time(df):
    """Groups data into 1-second chunks based on Real-World time (if available)."""
    abs_time = get_absolute_time_series(df)
    if abs_time is not None:
        df['__sync_time__'] = abs_time.round(0)
    else:
        t_col = dh.get_time_col(df)
        if t_col:
            df['__sync_time__'] = pd.to_numeric(df[t_col], errors='coerce')
            df = df.dropna(subset=['__sync_time__'])
            df['__sync_time__'] = (df['__sync_time__'] - df['__sync_time__'].min()).round(0)
        else:
            df['__sync_time__'] = df.index

    agg_dict = {c: 'mean' if pd.api.types.is_numeric_dtype(df[c]) else 'first' for c in df.columns if
                c != '__sync_time__'}
    return df.groupby('__sync_time__').agg(agg_dict)


def run_sync_engine(project_data, gap_rules, tdms_rules):
    mode = project_data.get("analysis_mode", "Merge DAQ + MAC")
    combined_map = {}
    summary_map = {}

    def apply_gap_treatment(df):
        df = df.replace(-1.0, np.nan)
        for c in df.select_dtypes(include=[np.number]).columns:
            if df[c].count() > 1:
                rule = gap_rules.get(c, "Interpolation")
                if rule == "Interpolation":
                    df[c] = df[c].interpolate(method='linear')
                elif rule == "Last Number":
                    df[c] = df[c].ffill()
        return df

    if "Merge" in mode:
        for i, pair in enumerate(project_data["pairs"]):
            dp, mp = pair.get("daq", ""), pair.get("mac", "")
            if not dp and not mp: continue

            df_d = dh.clean_data(dh.load_dataframe(dp, tdms_rules=tdms_rules)) if dp else pd.DataFrame()
            df_m = dh.clean_data(dh.load_dataframe(mp, tdms_rules=tdms_rules)) if mp else pd.DataFrame()

            if not df_d.empty: df_d = process_file_time(df_d)
            if not df_m.empty: df_m = process_file_time(df_m)

            df_d.columns = [c if str(c).startswith("Daq.") else f"Daq.{c}" for c in df_d.columns]
            df_m.columns = [c if str(c).startswith("Mac.") else f"Mac.{c}" for c in df_m.columns]

            if not df_d.empty and not df_m.empty:
                m_df = pd.concat([df_d, df_m], axis=1).sort_index()
            elif not df_d.empty:
                m_df = df_d.copy().sort_index()
            else:
                m_df = df_m.copy().sort_index()

            m_df = apply_gap_treatment(m_df)
            m_df = m_df.ffill().bfill().reset_index()

            # --- THE BUG FIX: Force rename the absolute timestamp column ---
            if '__sync_time__' in m_df.columns:
                m_df.rename(columns={'__sync_time__': 'Common_Time_Sec'}, inplace=True)
            elif 'index' in m_df.columns:
                m_df.rename(columns={'index': 'Common_Time_Sec'}, inplace=True)

            if 'Common_Time_Sec' in m_df.columns:
                m_df['Common_Time_Sec'] = pd.to_numeric(m_df['Common_Time_Sec'], errors='coerce')
                m_df = m_df.dropna(subset=['Common_Time_Sec'])
                m_df['Common_Time_Sec'] = m_df['Common_Time_Sec'] - m_df['Common_Time_Sec'].min()
                m_df['Common_Time_Sec'] = m_df['Common_Time_Sec'].round(3)

            cycle_base = pair.get("cycle", f"Cycle {i + 1}")
            cycle_id = f"{cycle_base}_Combined"
            m_df["Cycle_ID"] = cycle_id

            ft_code = pair.get("fuel_type", "E")
            if ft_code == "E":
                ft = "Electric"
            elif ft_code == "G":
                ft = "Gas"
            elif ft_code == "HP":
                ft = "Heat Pump"
            else:
                ft = "Undefined"

            summary_row = {"Cycle_ID": cycle_id, "Fuel_Type": ft}
            summary_row["Source_DAQ_File"] = dp if dp else ""
            summary_row["Source_MAC_File"] = mp if mp else ""
            summary_row["Missing_Data_Source"] = "None" if (dp and mp) else ("DAQ Missing" if not dp else "MAC Missing")

            mac_meta = dh.extract_mac_metadata(mp) if mp else {"Appliance": "", "Model": "", "Serial": "",
                                                               "FW Release": ""}
            summary_row["Meta_Appliance"] = mac_meta["Appliance"];
            summary_row["Meta_Model"] = mac_meta["Model"]
            summary_row["Meta_Serial"] = mac_meta["Serial"];
            summary_row["Meta_FW_Release"] = mac_meta["FW Release"]

            daq_meta = dh.extract_daq_metadata(dp) if dp else {"DAQ_Station": "", "DAQ_Device": ""}
            summary_row["Meta_DAQ_Station"] = daq_meta["DAQ_Station"];
            summary_row["Meta_DAQ_Device"] = daq_meta["DAQ_Device"]

            for k, v in pair.get("tuning_data", {}).items():
                m_df[f"Tun.{k}"] = v;
                summary_row[f"Tun.{k}"] = v

            combined_map[cycle_id] = m_df
            summary_map[cycle_id] = summary_row

    else:
        tag = "DAQ" if "DAQ" in mode else "MAC"
        prefix = tag.capitalize()
        for i, f in enumerate([f for f in project_data["files"] if project_data["file_types"].get(f) == tag]):
            df = dh.clean_data(dh.load_dataframe(f, tdms_rules=tdms_rules))
            if not df.empty: df = process_file_time(df)
            df = df.reset_index()

            # --- THE BUG FIX: Force rename the absolute timestamp column ---
            if '__sync_time__' in df.columns:
                df.rename(columns={'__sync_time__': 'Common_Time_Sec'}, inplace=True)
            elif 'index' in df.columns:
                df.rename(columns={'index': 'Common_Time_Sec'}, inplace=True)

            df.columns = [c if (c == 'Common_Time_Sec' or str(c).startswith(f"{prefix}.")) else f"{prefix}.{c}" for c in
                          df.columns]
            df = apply_gap_treatment(df)
            df = df.ffill().bfill()

            if 'Common_Time_Sec' in df.columns:
                df['Common_Time_Sec'] = pd.to_numeric(df['Common_Time_Sec'], errors='coerce')
                df = df.dropna(subset=['Common_Time_Sec'])
                df['Common_Time_Sec'] = df['Common_Time_Sec'] - df['Common_Time_Sec'].min()
                df['Common_Time_Sec'] = df['Common_Time_Sec'].round(3)

            cycle_base = f"{os.path.basename(f).split('.')[0]}_{i + 1}"
            cycle_id = f"{cycle_base}_Combined"
            df["Cycle_ID"] = cycle_id

            nl = f.lower()
            if "heatpump" in nl or "hhp" in nl:
                ft = "Heat Pump"
            elif "gas" in nl:
                ft = "Gas"
            elif "elec" in nl:
                ft = "Electric"
            else:
                m = re.search(r'(\d+)\s*v', nl)
                if m and int(m.group(1)) < 170:
                    ft = "Gas"
                else:
                    ft = "Electric"

            summary_row = {"Cycle_ID": cycle_id, "Fuel_Type": ft}
            summary_row["Source_DAQ_File"] = f if tag == "DAQ" else ""
            summary_row["Source_MAC_File"] = f if tag == "MAC" else ""
            summary_row["Missing_Data_Source"] = "MAC Missing" if tag == "DAQ" else "DAQ Missing"

            mac_meta = dh.extract_mac_metadata(f) if tag == "MAC" else {"Appliance": "", "Model": "", "Serial": "",
                                                                        "FW Release": ""}
            summary_row["Meta_Appliance"] = mac_meta["Appliance"];
            summary_row["Meta_Model"] = mac_meta["Model"]
            summary_row["Meta_Serial"] = mac_meta["Serial"];
            summary_row["Meta_FW_Release"] = mac_meta["FW Release"]

            daq_meta = dh.extract_daq_metadata(f) if tag == "DAQ" else {"DAQ_Station": "", "DAQ_Device": ""}
            summary_row["Meta_DAQ_Station"] = daq_meta["DAQ_Station"];
            summary_row["Meta_DAQ_Device"] = daq_meta["DAQ_Device"]

            combined_map[cycle_id] = df
            summary_map[cycle_id] = summary_row

    return combined_map, summary_map

