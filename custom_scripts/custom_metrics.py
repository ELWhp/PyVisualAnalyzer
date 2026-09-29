import pandas as pd
import numpy as np


def run_custom_math(df, summary_row):
    # =========================================================
    # 1. SAFELY IDENTIFY THE COLUMNS
    # =========================================================
    cond_col = None
    for col in ["Percent_Conductivity", "Percent Conductivity", "Mac.Moisture_Sensor_Percent_Conductivity"]:
        if col in df.columns:
            cond_col = col
            break

    time_elapsed_col = None
    for col in ["Time_Elapsed", "TimeElapsed", "Time Elapsed", "Mac.Time_Elapsed"]:
        if col in df.columns:
            time_elapsed_col = col
            break

    # =========================================================
    # 2. OVERWRITE LOGIC & SLOPE (70 and 60)
    # =========================================================
    if cond_col and time_elapsed_col:
        # Convert to numeric to ensure clean math
        curr_cond = pd.to_numeric(df[cond_col], errors='coerce')
        curr_time = pd.to_numeric(df[time_elapsed_col], errors='coerce')
        prev_cond = curr_cond.shift(1)

        # Create transition masks (True when previous > target AND current <= target)
        mask_70 = (prev_cond > 70) & (curr_cond <= 70)
        mask_60 = (prev_cond > 60) & (curr_cond <= 60)

        # Fallback just in case the dryer started already below 70 or 60 from second 0
        if not curr_cond.empty and pd.notna(curr_cond.iloc[0]):
            if curr_cond.iloc[0] <= 70: mask_70.iloc[0] = True
            if curr_cond.iloc[0] <= 60: mask_60.iloc[0] = True

        idx_60 = None
        idx_70 = None




        # 1. Find the FIRST time it drops to 60 (This locks the overwrite)
        if mask_60.any():
            idx_60 = mask_60.idxmax()

            # 2. Find the LAST time it dropped to 70 BEFORE or AT the 60 lock
            valid_70s = mask_70.loc[:idx_60]
            if valid_70s.any():
                idx_70 = valid_70s[valid_70s].index[-1]  # [-1] gets the last occurrence (overwriting previous ones)

        else:
            # If 60 never happened, it just keeps overwriting 70 until the end of the file
            if mask_70.any():
                idx_70 = mask_70[mask_70].index[-1]

        # --- SAVE TIMES ---
        if idx_70 is not None:
            time_70 = curr_time.loc[idx_70]
            summary_row["Time_to_70_PC"] = time_70
        else:
            summary_row["Time_to_70_PC"] = "Never reached 70%"

        if idx_60 is not None:
            time_60 = curr_time.loc[idx_60]
            summary_row["Time_to_60_PC"] = time_60
            if pd.notna(time_60):
                summary_row["Time_to_60_PC_min"] = round(time_60 / 60, 2)
            else:
                summary_row["Time_to_60_PC_min"] = "Invalid time data"
        else:
            summary_row["Time_to_60_PC"] = "Never reached 60%"
            summary_row["Time_to_60_PC_min"] = "Never reached 60%"

        # --- CALCULATE SLOPE ---
        if idx_70 is not None and idx_60 is not None:
            t1 = curr_time.loc[idx_70]
            c1 = curr_cond.loc[idx_70]