import pandas as pd
import numpy as np


def run_custom_math(df, summary_row):
    # --- WIPE OLD ERRORS FROM MEMORY SO THE UI DOESN'T SPAM WARNINGS ---
    if "Custom_Script_Error" in summary_row:
        del summary_row["Custom_Script_Error"]

    try:
        # =========================================================
        # 1. SAFELY IDENTIFY THE COLUMNS
        # =========================================================
        cond_col = next(
            (c for c in ["Percent_Conductivity", "Percent Conductivity", "Mac.Moisture_Sensor_Percent_Conductivity"] if
             c in df.columns), None)
        time_elapsed_col = next(
            (c for c in ["Time_Elapsed", "TimeElapsed", "Time Elapsed", "Mac.Time_Elapsed"] if c in df.columns), None)
        sys_state_col = next((c for c in ["SystemState", "Mac.SystemState", "System_State"] if c in df.columns), None)
        synth_col_found = next(
            (c for c in ["Synthetic_Detection_Status", "Mac.Synthetic_Detection_Status"] if c in df.columns), None)

        main_time_col = "Common_Time_Sec" if "Common_Time_Sec" in df.columns else time_elapsed_col

        # --- ESTABLISH THE SYNTHETIC DETECTION TIME GATE ---
        t_synth_start = 50  # Default 50s warmup fallback if the column doesn't exist

        if synth_col_found and main_time_col:
            synth_numeric = pd.to_numeric(df[synth_col_found], errors='coerce')
            synth_hit = df[synth_numeric >= 1]
            if not synth_hit.empty:
                t_synth_start = synth_hit.iloc[0][main_time_col]
                # Keep the 50s minimum safety net just in case it triggers instantly
                if t_synth_start < 50:
                    t_synth_start = 50
            else:
                # If the column exists but NEVER hits >= 1, lock out the conductivity checks entirely
                t_synth_start = float('inf')

        # =========================================================
        # 2. STATE MACHINE LOOP: TRACKING 80, 75, 70, 65, 63, AND 60
        # =========================================================
        try:
            if cond_col and time_elapsed_col:
                conds = pd.to_numeric(df[cond_col], errors='coerce').values
                times = pd.to_numeric(df[time_elapsed_col], errors='coerce').values
                rmcs = pd.to_numeric(df["Estimated_RMC"],
                                     errors='coerce').values if "Estimated_RMC" in df.columns else None

                time_80 = time_75 = time_70 = time_65 = time_63 = time_60 = None
                cond_80 = cond_75 = cond_70 = cond_65 = cond_63 = cond_60 = None
                rmc_80 = rmc_75 = rmc_70 = rmc_65 = rmc_63 = rmc_60 = None

                for i in range(1, len(conds)):
                    prev_c, curr_c, curr_t = conds[i - 1], conds[i], times[i]
                    curr_rmc = rmcs[i] if rmcs is not None else None

                    # NEW GATE: Completely ignore all readings before t_synth_start
                    if pd.isna(prev_c) or pd.isna(curr_c) or pd.isna(curr_t) or curr_t < t_synth_start:
                        continue

                    if prev_c > 80 and curr_c <= 80: time_80, cond_80, rmc_80 = curr_t, curr_c, curr_rmc
                    if time_80 is not None and prev_c > 75 and curr_c <= 75: time_75, cond_75, rmc_75 = curr_t, curr_c, curr_rmc
                    if time_75 is not None and prev_c > 70 and curr_c <= 70: time_70, cond_70, rmc_70 = curr_t, curr_c, curr_rmc
                    if time_70 is not None and prev_c > 65 and curr_c <= 65: time_65, cond_65, rmc_65 = curr_t, curr_c, curr_rmc
                    if time_65 is not None and prev_c > 63 and curr_c <= 63: time_63, cond_63, rmc_63 = curr_t, curr_c, curr_rmc
                    if time_63 is not None and prev_c > 60 and curr_c <= 60:
                        time_60, cond_60, rmc_60 = curr_t, curr_c, curr_rmc
                        break

                summary_row["Time_to_65_PC_sec"] = time_65 if time_65 is not None else "Never reached 65"
                summary_row["Time_to_65_PC_min"] = round(time_65 / 60.0, 2) if time_65 is not None else "N/A"
                summary_row["Time_to_63_PC_sec"] = time_63 if time_63 is not None else "Never reached 63"
                summary_row["Time_to_63_PC_min"] = round(time_63 / 60.0, 2) if time_63 is not None else "N/A"

                # --- NEW: EXTRACT RMC AT TARGET CONDUCTIVITIES (FIXED NUMPY TRAP) ---
                def safe_round_rmc(val):
                    try:
                        return round(float(val), 2) if pd.notna(val) else "N/A"
                    except:
                        return "N/A"

                summary_row["RMC_at_75_PC"] = safe_round_rmc(rmc_75)
                summary_row["RMC_at_70_PC"] = safe_round_rmc(rmc_70)
                summary_row["RMC_at_65_PC"] = safe_round_rmc(rmc_65)
                summary_row["RMC_at_63_PC"] = safe_round_rmc(rmc_63)

                # --- NEW: EXACT TIME DELTA BETWEEN 70 AND 63 ---
                if time_70 is not None and time_63 is not None:
                    summary_row["Time_Shift_70_to_63_min"] = round((time_63 - time_70) / 60.0, 2)
                else:
                    summary_row["Time_Shift_70_to_63_min"] = "Missing 70 or 63 target"

                def calc_slope(t1, t2, c1, c2, name):
                    if t1 is not None and t2 is not None:
                        dt = t2 - t1
                        summary_row[name] = round(((c2 - c1) / dt) * 60, 4) if dt > 0 else "Time delta is 0"
                    else:
                        summary_row[name] = "Missing target"

                calc_slope(time_80, time_75, cond_80, cond_75, "Slope_80_to_75_PC_per_min")
                calc_slope(time_75, time_70, cond_75, cond_70, "Slope_75_to_70_PC_per_min")
                calc_slope(time_70, time_65, cond_70, cond_65, "Slope_70_to_65_PC_per_min")
                calc_slope(time_70, time_60, cond_70, cond_60, "Slope_70_to_60_PC_per_min")

            else:
                for k in ["Time_to_65_PC_sec", "Time_to_63_PC_sec", "Time_Shift_70_to_63_min",
                          "Slope_80_to_75_PC_per_min", "Slope_75_to_70_PC_per_min", "Slope_70_to_65_PC_per_min",
                          "Slope_70_to_60_PC_per_min", "RMC_at_75_PC", "RMC_at_70_PC", "RMC_at_65_PC",
                          "RMC_at_63_PC"]:
                    summary_row[k] = "Cond/Time not mapped"
        except Exception as e:
            for k in ["Time_to_65_PC_sec", "Time_to_63_PC_sec", "Time_Shift_70_to_63_min",
                      "Slope_80_to_75_PC_per_min", "Slope_75_to_70_PC_per_min", "Slope_70_to_65_PC_per_min",
                      "Slope_70_to_60_PC_per_min", "RMC_at_75_PC", "RMC_at_70_PC", "RMC_at_65_PC",
                      "RMC_at_63_PC"]:
                summary_row[k] = f"failed here bc of {str(e)}"

        # =========================================================
        # 3. SYNTHETIC DETECTION STATUS EXTRACTOR
        # =========================================================
        try:
            if synth_col_found:
                unique_vals = df[synth_col_found].dropna().unique()
                clean_vals = [str(int(float(v))) if float(v).is_integer() else str(v) for v in unique_vals]
                summary_row["Synthetic_Detection_Statuses_Seen"] = ", ".join(sorted(set(clean_vals)))

                synth_hit = df[pd.to_numeric(df[synth_col_found], errors='coerce') >= 1]
                summary_row["Cond_at_First_Synth_1"] = synth_hit.iloc[0][
                    cond_col] if not synth_hit.empty and cond_col else "Never hit >= 1"
            else:
                summary_row["Synthetic_Detection_Statuses_Seen"] = "Column not mapped"
                summary_row["Cond_at_First_Synth_1"] = "Column not mapped"
        except Exception as e:
            summary_row["Synthetic_Detection_Statuses_Seen"] = f"failed here bc of {str(e)}"
            summary_row["Cond_at_First_Synth_1"] = f"failed here bc of {str(e)}"

        # =========================================================
        # 4. EXTRACT MAXIMUM VALUE OF TIME ELAPSED
        # =========================================================
        try:
            if time_elapsed_col:
                max_time = pd.to_numeric(df[time_elapsed_col], errors='coerce').max()
                summary_row["Max_Time_Elapsed"] = round(max_time, 1) if pd.notna(max_time) else "Invalid data"
                summary_row["Max_Time_Elapsed_min"] = round(max_time / 60.0, 2) if pd.notna(
                    max_time) else "Invalid data"
            else:
                summary_row["Max_Time_Elapsed"] = "Column not found"
                summary_row["Max_Time_Elapsed_min"] = "Column not found"
        except Exception as e:
            summary_row["Max_Time_Elapsed"] = f"failed here bc of {str(e)}"
            summary_row["Max_Time_Elapsed_min"] = f"failed here bc of {str(e)}"

        # =========================================================
        # 5. END OF CYCLE (SYSTEM STATE == 4) & STATE 2 MAX COND
        # =========================================================
        t_end_sec = None
        try:
            if sys_state_col:
                sys_state_numeric = pd.to_numeric(df[sys_state_col], errors='coerce')

                if main_time_col:
                    end_hit = df[sys_state_numeric == 4]
                    if not end_hit.empty:
                        t_end_sec = end_hit.iloc[0][main_time_col]
                        summary_row["End_of_Cycle_State_4_sec"] = t_end_sec
                        summary_row["End_of_Cycle_State_4_min"] = round(t_end_sec / 60.0, 2)
                    else:
                        summary_row["End_of_Cycle_State_4_sec"] = "Never hit State 4"
                        summary_row["End_of_Cycle_State_4_min"] = "Never hit State 4"

                if cond_col:
                    state_2_hit = df[sys_state_numeric == 2]
                    summary_row["Max_Cond_State_2"] = pd.to_numeric(state_2_hit[cond_col],
                                                                    errors='coerce').max() if not state_2_hit.empty else "Never hit State 2"
            else:
                summary_row["End_of_Cycle_State_4_sec"] = "State col missing"
                summary_row["Max_Cond_State_2"] = "State col missing"
        except Exception as e:
            summary_row["End_of_Cycle_State_4_sec"] = f"failed here bc of {str(e)}"
            summary_row["End_of_Cycle_State_4_min"] = f"failed here bc of {str(e)}"
            summary_row["Max_Cond_State_2"] = f"failed here bc of {str(e)}"

        # =========================================================
        # 6. RMC TARGETS & LOOK-BACK MATH
        # =========================================================
        try:
            if "Estimated_RMC" in df.columns and main_time_col:
                rmc_series = pd.to_numeric(df["Estimated_RMC"], errors='coerce')

                def extract_rmc(target):
                    try:
                        hit = df[rmc_series <= target]
                        if not hit.empty:
                            t_sec = hit.iloc[0][main_time_col]
                            summary_row[f"Time_to_{target}_RMC_sec"] = t_sec
                            summary_row[f"Time_to_{target}_RMC_min"] = round(t_sec / 60.0, 2) if pd.notna(
                                t_sec) else "N/A"
                            summary_row[f"Cond_at_{target}_RMC"] = hit.iloc[0][
                                cond_col] if cond_col else "Cond col missing"
                            return t_sec
                        else:
                            summary_row[f"Time_to_{target}_RMC_sec"] = f"Never reached {target}"
                            summary_row[f"Time_to_{target}_RMC_min"] = f"Never reached {target}"
                            summary_row[f"Cond_at_{target}_RMC"] = "N/A"
                            return None
                    except Exception as e:
                        summary_row[f"Time_to_{target}_RMC_sec"] = f"failed here bc of {str(e)}"
                        summary_row[f"Time_to_{target}_RMC_min"] = f"failed here bc of {str(e)}"
                        summary_row[f"Cond_at_{target}_RMC"] = f"failed here bc of {str(e)}"
                        return None

                t_1_8_sec = extract_rmc(1.8)
                t_1_6_sec = extract_rmc(1.6)
                t_1_5_sec = extract_rmc(1.5)
                t_1_4_sec = extract_rmc(1.4)
                t_1_25_sec = extract_rmc(1.25)

                # Find time when Cond <= 32% (Filtered by t_synth_start!)
                t_cond_32_sec = None
                if cond_col:
                    try:
                        cond_series = pd.to_numeric(df[cond_col], errors='coerce')
                        time_series = pd.to_numeric(df[main_time_col], errors='coerce')
                        # Only look for 32% AFTER synthetic detection triggered
                        cond_32_hit = df[(cond_series <= 32) & (time_series >= t_synth_start)]

                        if not cond_32_hit.empty:
                            t_cond_32_sec = cond_32_hit.iloc[0][main_time_col]
                    except Exception as e:
                        pass  # Silently pass if it fails, downstream lookback will safely catch it

                def calculate_lookback(delta_name, t_target):
                    try:
                        if pd.notna(t_end_sec) and pd.notna(t_target):
                            delta_sec = t_end_sec - t_target
                            summary_row[f"Time_{delta_name}_to_End_min"] = round(delta_sec / 60.0, 2)

                            if pd.notna(t_cond_32_sec):
                                target_time = t_cond_32_sec - delta_sec
                                if target_time >= 0 and cond_col:
                                    after_target = df[pd.to_numeric(df[main_time_col], errors='coerce') >= target_time]
                                    summary_row[f"Cond_at_Cond32_minus_delta_{delta_name}"] = after_target.iloc[0][
                                        cond_col] if not after_target.empty else "Time out of bounds"
                                else:
                                    summary_row[f"Cond_at_Cond32_minus_delta_{delta_name}"] = "Target time < 0"
                            else:
                                summary_row[
                                    f"Cond_at_Cond32_minus_delta_{delta_name}"] = "Never hit 32% Cond (after synth)"
                        else:
                            summary_row[
                                f"Time_{delta_name}_to_End_min"] = f"Missing {delta_name} target or End of Cycle"
                            summary_row[
                                f"Cond_at_Cond32_minus_delta_{delta_name}"] = f"Missing {delta_name} target or End of Cycle"
                    except Exception as e:
                        summary_row[f"Time_{delta_name}_to_End_min"] = f"failed here bc of {str(e)}"
                        summary_row[f"Cond_at_Cond32_minus_delta_{delta_name}"] = f"failed here bc of {str(e)}"

                calculate_lookback("1.8", t_1_8_sec)
                calculate_lookback("1.6", t_1_6_sec)
                calculate_lookback("1.5", t_1_5_sec)
                calculate_lookback("1.4", t_1_4_sec)
                calculate_lookback("1.25", t_1_25_sec)

        except Exception as e:
            summary_row["RMC_Math_Status"] = f"failed here bc of {str(e)}"

        summary_row["DEBUG_STATUS"] = "SUCCESS"

    except Exception as e:
        summary_row["DEBUG_STATUS"] = f"CRITICAL FAILURE: {str(e)}"

    return df, summary_row