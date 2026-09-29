import pandas as pd
import numpy as np
import importlib.util
import os


def calc_mixing_ratio(temp_c, rh):
    """Calculates Absolute Humidity (Mixing Ratio in g/kg) from Temp (C) and Rel. Humidity (%)."""
    p_sat = 6.112 * np.exp((17.67 * temp_c) / (temp_c + 243.5))
    p_v = p_sat * (rh / 100.0)
    p_atm = 1013.25
    mr = 621.99 * (p_v / (p_atm - p_v))
    return mr


def get_single_value(col_name, mapping, df, sum_row):
    """Extracts value and converts to KG if it is a weight."""
    src = mapping.get(f"{col_name}_src", "Tuning Book")
    val = mapping.get(f"{col_name}_val", 0)

    raw_val = 0
    scale_col = mapping.get("Raw_Scale_Weight", "")

    if src == "Use First Scale Weight":
        raw_val = df[scale_col].iloc[0] if scale_col and scale_col in df.columns and not df.empty else 0
        unit = "kg"
    elif src == "Use Last Scale Weight":
        raw_val = df[scale_col].iloc[-1] if scale_col and scale_col in df.columns and not df.empty else 0
        unit = "kg"
    elif src == "Tuning Book":
        try:
            raw_val = float(sum_row.get(f"Tun.{col_name}", sum_row.get(f"Tun.{val}", 0)))
        except:
            raw_val = 0
    else:
        try:
            raw_val = float(val)
        except:
            raw_val = 0

    # --- THE MATH BUG FIX ---
    # Intercept RMC right here so it doesn't get converted into pounds/kilos
    if "RMC" in col_name:
        safe_name = col_name.replace(" ", "_")
        sum_row[f"{safe_name}"] = raw_val
        return raw_val

    # Only apply weight conversions if it is not an RMC variable
    unit = mapping.get(f"{col_name}_unit", "lb")
    if unit == "lb":
        val_kg = raw_val * 0.453592
        val_lb = raw_val
    else:
        val_kg = raw_val
        val_lb = raw_val * 2.20462

    safe_name = col_name.replace(" ", "_")
    sum_row[f"{safe_name}_kg"] = val_kg
    sum_row[f"{safe_name}_lb"] = val_lb

    return val_kg

def run_analysis(df_in, mappings, sum_row, alpha=0.95, custom_script_path=""):
    """Core Analytics Engine."""
    df = df_in.copy()

    ts_slots = [
        "Mac_Cycle", "Raw_Scale_Weight", "Inlet_Vaisala_Temp", "Inlet_Vaisala_Humidity", "Exhaust_Vaisala_Temp",
        "Exhaust_Vaisala_Humidity", "Inlet_Vaisala_MR", "Exhaust_Vaisala_MR", "Power_Phase_1", "Power_Phase_2"
    ]
    for slot in ts_slots + mappings.get("custom_slots", []):
        mapped_col = mappings.get(slot, "")
        if mapped_col and mapped_col in df.columns:
            df[slot] = df[mapped_col]

    # ==========================================
    # 1. CYCLE TRIMMING
    # ==========================================
    cycle_col = mappings.get("Mac_Cycle", "")
    if cycle_col and cycle_col in df.columns:
        if (df[cycle_col] == 0).all():
            sum_row["Cycle_Warning"] = "No cycle run (Mac Cycle always 0)"
        else:
            start_idx = df.index[df[cycle_col] > 0].min()
            if start_idx == df.index.min() and df.loc[start_idx, cycle_col] > 0:
                sum_row["Cycle_Warning"] = "Log began recording late"

            after_start = df.loc[start_idx:]
            end_idx_candidates = after_start.index[(after_start[cycle_col] == 0) | (after_start[cycle_col] > 100)]

            if end_idx_candidates.empty:
                sum_row["Cycle_Warning"] = "Warning: Cycle did not properly end"
                end_idx = after_start.index.max()
            else:
                end_idx = end_idx_candidates.min()

            df = df.loc[start_idx:end_idx].copy().reset_index(drop=True)

    # ==========================================
    # 2. RAW WEIGHT & IIR FILTER
    # ==========================================
    weight_opt = mappings.get("Scale_Weight_Method", "1. Use Raw Scale Data")
    scale_col = mappings.get("Raw_Scale_Weight", "")

    w_base = pd.Series(0, index=df.index)
    if scale_col and scale_col in df.columns:
        w_base = df[scale_col]

    if "2. Filter" in weight_opt and len(w_base) > 0:
        pandas_alpha = 1.0 - alpha
        w_base = w_base.ewm(alpha=pandas_alpha, adjust=False).mean()

    df["Scale_Weight_kg"] = w_base

    # ==========================================
    # 3. SCALE DATA ADJUSTMENT (FINAL WEIGHT)
    # ==========================================
    adj_opt = mappings.get("Adjusted_Weight_Method", "1. Use Scale_Weight_Method")
    start_w_kg = get_single_value("Start_Weight", mappings, df, sum_row)
    end_w_kg = get_single_value("End_Weight", mappings, df, sum_row)

    final_weight_kg = w_base.copy()

    if "2. Use Vaisalas" in adj_opt:
        mr_in_col = mappings.get("Inlet_Vaisala_MR", "")
        mr_ex_col = mappings.get("Exhaust_Vaisala_MR", "")

        mr_in = df[mr_in_col] if mr_in_col in df.columns else None
        mr_ex = df[mr_ex_col] if mr_ex_col in df.columns else None

        if mr_in is None or mr_in_col == "Calculate from Temp/RH":
            tc_in, rh_in = mappings.get("Inlet_Vaisala_Temp", ""), mappings.get("Inlet_Vaisala_Humidity", "")
            if tc_in in df.columns and rh_in in df.columns:
                mr_in = calc_mixing_ratio(df[tc_in], df[rh_in])
                df["Calculated_Inlet_MR"] = mr_in

        if mr_ex is None or mr_ex_col == "Calculate from Temp/RH":
            tc_ex, rh_ex = mappings.get("Exhaust_Vaisala_Temp", ""), mappings.get("Exhaust_Vaisala_Humidity", "")
            if tc_ex in df.columns and rh_ex in df.columns:
                mr_ex = calc_mixing_ratio(df[tc_ex], df[rh_ex])
                df["Calculated_Exhaust_MR"] = mr_ex

        if mr_in is not None and mr_ex is not None:
            mr_diff = (mr_ex - mr_in)
            cumsum = mr_diff.cumsum()
            c_start = cumsum.iloc[0]
            c_end = cumsum.iloc[-1]

            if c_end != c_start:
                norm_c = (cumsum - c_start) / (c_end - c_start)
                final_weight_kg = start_w_kg - norm_c * (start_w_kg - end_w_kg)
            else:
                final_weight_kg = pd.Series(start_w_kg, index=df.index)
        else:
            sum_row["Cycle_Warning"] = "Vaisala mapping failed, defaulted to Raw Scale."

    elif "3. Use Scale_Weight_Method" in adj_opt:
        if not w_base.empty:
            s_max = w_base.iloc[0]
            s_min = w_base.iloc[-1]
            if s_max != s_min:
                norm_w = (s_max - w_base) / (s_max - s_min)
                final_weight_kg = start_w_kg - norm_w * (start_w_kg - end_w_kg)
            else:
                final_weight_kg = pd.Series(start_w_kg, index=df.index)

    df["Adjusted_Weight_kg"] = final_weight_kg.clip(lower=0)

    # ==========================================
    # 4. RMC CALCULATION
    # ==========================================
    rmc_opt = mappings.get("Estimated_RMC_Method", "1. Use Adjusted_Weight_Method & Bone Dry Weight")

    if "1. Use" in rmc_opt:
        bd_w_kg = get_single_value("Bone_Dry_Weight", mappings, df, sum_row)
        if bd_w_kg > 0:
            df["Estimated_RMC"] = ((df["Adjusted_Weight_kg"] - bd_w_kg) / bd_w_kg) * 100
        else:
            df["Estimated_RMC"] = 0

    elif "2. Use" in rmc_opt:
        start_rmc = get_single_value("Start_RMC", mappings, df, sum_row)
        end_rmc = get_single_value("End_RMC", mappings, df, sum_row)
        if not df["Adjusted_Weight_kg"].empty:
            fw_start = df["Adjusted_Weight_kg"].iloc[0]
            fw_end = df["Adjusted_Weight_kg"].iloc[-1]
            if fw_start != fw_end:
                norm_fw = (fw_start - df["Adjusted_Weight_kg"]) / (fw_start - fw_end)
                df["Estimated_RMC"] = start_rmc - (norm_fw * (start_rmc - end_rmc))
            else:
                df["Estimated_RMC"] = start_rmc

    # EXTRACT ACTUAL START & END RMC TO SUMMARY FILE
    if "Estimated_RMC" in df.columns and not df.empty:
        sum_row["Output_Start_RMC"] = df["Estimated_RMC"].iloc[0]
        sum_row["Output_End_RMC"] = df["Estimated_RMC"].iloc[-1]

    # ==========================================
    # 5. TOTAL POWER ENGINE
    # ==========================================
    full_fuel = sum_row.get("Fuel_Type", "Electric")

    p1_col, p1_u = mappings.get("Power_Phase_1", ""), mappings.get("Power_Phase_1_unit", "W")
    p2_col, p2_u = mappings.get("Power_Phase_2", ""), mappings.get("Power_Phase_2_unit", "W")

    p1_val = df[p1_col] if p1_col in df.columns else pd.Series(0, index=df.index)
    p2_val = df[p2_col] if p2_col in df.columns else pd.Series(0, index=df.index)

    if p1_u == "kW": p1_val = p1_val * 1000
    if p2_u == "kW": p2_val = p2_val * 1000

    df["Total_Power"] = p1_val + p2_val

    if full_fuel == "Undefined":
        df["Estimated_Avg_Motor_Power"] = np.nan
        df["Estimated_Heater_Power"] = np.nan
        df["Estimated_Power_Absorbed_Load"] = np.nan
        df["Calculated_Heater_Status"] = np.nan
    else:
        if df["Total_Power"].mean() > 0:
            df["Estimated_Avg_Motor_Power"] = 270
            df["Estimated_Heater_Power"] = df["Total_Power"] - 270
            df["Estimated_Power_Absorbed_Load"] = df["Estimated_Heater_Power"] * 0.8
            df["Calculated_Heater_Status"] = np.where(df["Total_Power"] > 4000, 1, 0)

    # ==========================================
    # 6. POST-PROCESSING CUSTOM SCRIPT ENGINE
    # ==========================================
    if custom_script_path and os.path.exists(custom_script_path):
        try:
            spec = importlib.util.spec_from_file_location("custom_math", custom_script_path)
            custom_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(custom_module)

            if hasattr(custom_module, "run_custom_math"):
                df, sum_row = custom_module.run_custom_math(df, sum_row)
        except Exception as e:
            sum_row["Custom_Script_Error"] = str(e)
            print(f"Custom Script Failed: {e}")

    return df, sum_row