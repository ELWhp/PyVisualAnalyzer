import pandas as pd
import numpy as np
import zipfile
import glob
import warnings
from sklearn.linear_model import LinearRegression

# Suppress harmless pandas/math warnings for a clean console
warnings.filterwarnings("ignore")

# ==========================================
# 1. LAB CONSTANTS (UPDATE THESE FOR YOUR RUN)
# ==========================================
BONE_DRY_WEIGHT_LBS = 8.45  # The weight of the bone-dry D2 load in lbs
STANDBY_POWER_KWH = 0.05  # Your lab's measured standby/idle energy per cycle
MAX_TEMP_C = 53.0  # Your safety temp limit
MAX_TIME_MIN = 80.0  # DOE time limit
# ==========================================

# ==========================================
# 2. RMC CALIBRATION DATA
# Put your historical manual data here so the
# script can map Conductivity to actual Water Weight
# ==========================================
RMC_HISTORY = pd.DataFrame([
    {'end_cond_thresh': 50, 'rmc': 2.20},
    {'end_cond_thresh': 50, 'rmc': 2.20},
    {'end_cond_thresh': 40, 'rmc': 1.18},
    {'end_cond_thresh': 40, 'rmc': 1.38}
])


def train_rmc_model():
    """Trains a model to predict RMC based solely on the End Conductivity Threshold."""
    X = RMC_HISTORY[['end_cond_thresh']].values
    y = RMC_HISTORY['rmc'].values
    model = LinearRegression().fit(X, y)
    return model


def extract_mac_data(mac_filepath):
    """Unzips the .mac file and loads the Acquisition log."""
    print(f"Reading MAC file: {mac_filepath}")
    try:
        with zipfile.ZipFile(mac_filepath, 'r') as z:
            target_file = [f for f in z.namelist() if f.endswith('.txt') or f.endswith('.csv')][0]
            with z.open(target_file) as f:
                try:
                    df = pd.read_csv(f, low_memory=False)
                    if 'Phase' not in df.columns:
                        f.seek(0)
                        df = pd.read_csv(f, sep='\t', low_memory=False)
                except:
                    f.seek(0)
                    df = pd.read_csv(f, sep='\t', low_memory=False)
    except zipfile.BadZipFile:
        try:
            df = pd.read_csv(mac_filepath, low_memory=False)
            if 'Phase' not in df.columns:
                df = pd.read_csv(mac_filepath, sep='\t', low_memory=False)
        except Exception as e:
            print(f"ERROR: Could not read MAC file. Details: {e}")
            return pd.DataFrame()

    df.columns = df.columns.str.strip()
    return df


def extract_daq_data(daq_filepath):
    """Loads the DAQ power log."""
    print(f"Reading DAQ file: {daq_filepath}")
    df = pd.read_csv(daq_filepath, low_memory=False)
    df.columns = df.columns.str.strip()
    return df


def calculate_rates():
    """Extracts physical drying, heating, and power rates from the logs."""
    mac_files = glob.glob("*.mac")
    daq_files = glob.glob("*.csv")

    if not mac_files or not daq_files:
        print("ERROR: Please ensure at least one .mac and one .csv DAQ file are in the same folder as this script.")
        return None

    mac_df = extract_mac_data(mac_files[0])
    daq_df = extract_daq_data(daq_files[0])

    if mac_df.empty or daq_df.empty:
        return None

    # Extract Phase 3, Step 1 (Modulation) Rates
    mod_mask = (mac_df['Phase'] == 3) & (mac_df['Step'] == 1)
    mod_data = mac_df[mod_mask]

    # Extract Phase 3, Step 2 (Full Heat) Rates
    full_mask = (mac_df['Phase'] == 3) & (mac_df['Step'] == 2)
    full_data = mac_df[full_mask]

    if mod_data.empty or full_data.empty:
        print("ERROR: Could not find Phase 3 Step 1 or Step 2 in the MAC log.")
        return None

    mod_time_min = len(mod_data) / 60.0
    full_time_min = len(full_data) / 60.0

    # Slopes (Physical Rates per minute)
    mod_cond_drop = mod_data['Moisture_Sensor_Percent_Conductivity'].iloc[0] - \
                    mod_data['Moisture_Sensor_Percent_Conductivity'].iloc[-1]
    mod_cond_rate = mod_cond_drop / mod_time_min if mod_time_min > 0 else 0.1

    mod_temp_rise = mod_data['Probe_Front_Degrees_C_GI'].iloc[-1] - mod_data['Probe_Front_Degrees_C_GI'].iloc[0]
    mod_temp_rate = mod_temp_rise / mod_time_min if mod_time_min > 0 else 0.1

    full_cond_drop = full_data['Moisture_Sensor_Percent_Conductivity'].iloc[0] - \
                     full_data['Moisture_Sensor_Percent_Conductivity'].iloc[-1]
    full_cond_rate = full_cond_drop / full_time_min if full_time_min > 0 else 0.1

    full_temp_rise = full_data['Probe_Front_Degrees_C_GI'].iloc[-1] - full_data['Probe_Front_Degrees_C_GI'].iloc[0]
    full_temp_rate = full_temp_rise / full_time_min if full_time_min > 0 else 0.1

    # DAQ Power Alignment (Proportional to account for sampling rate differences)
    mod_end_row = mod_data.index[-1]
    full_end_row = full_data.index[-1]

    ratio = len(daq_df) / len(mac_df)

    daq_mod_end_idx = min(int(mod_end_row * ratio), len(daq_df) - 1)
    daq_full_end_idx = min(int(full_end_row * ratio), len(daq_df) - 1)

    total_energy_wh = daq_df['Total Energy Wh'].ffill().fillna(0).values

    energy_at_start = total_energy_wh[0]
    energy_at_mod_end = total_energy_wh[daq_mod_end_idx]
    energy_at_full_end = total_energy_wh[daq_full_end_idx]

    mod_energy_rate_wh = (energy_at_mod_end - energy_at_start) / mod_time_min if mod_time_min > 0 else 0
    full_energy_rate_wh = (energy_at_full_end - energy_at_mod_end) / full_time_min if full_time_min > 0 else 0

    print("\n--- EXTRACTED PHYSICAL RATES ---")
    print(
        f"Modulation: Cond Drops {mod_cond_rate:.2f}/min | Temp Rises {mod_temp_rate:.2f}°C/min | Pwr {mod_energy_rate_wh:.2f} Wh/min")
    print(
        f"Full Heat:  Cond Drops {full_cond_rate:.2f}/min | Temp Rises {full_temp_rate:.2f}°C/min | Pwr {full_energy_rate_wh:.2f} Wh/min\n")

    return {
        'initial_cond': mod_data['Moisture_Sensor_Percent_Conductivity'].iloc[0],
        'initial_temp': mod_data['Probe_Front_Degrees_C_GI'].iloc[0],
        'mod_cond_rate': mod_cond_rate, 'mod_temp_rate': mod_temp_rate, 'mod_energy_rate': mod_energy_rate_wh,
        'full_cond_rate': full_cond_rate, 'full_temp_rate': full_temp_rate, 'full_energy_rate': full_energy_rate_wh
    }


def simulate(rates, rmc_model):
    if not rates: return

    skip_range = np.arange(20, 100, 1)
    end_range = np.arange(1, 55, 1)

    results = []

    for skip in skip_range:
        for end in end_range:
            if skip <= end: continue

            # 1. Predict the final RMC based on this End Threshold
            predicted_rmc = rmc_model.predict(np.array([[end]]))[0]

            # Floor it at 0 (can't have negative water)
            predicted_rmc = max(0.0, predicted_rmc)

            # Skip combinations that fail the DOE >2.0% rule immediately
            if predicted_rmc > 2.0:
                continue

            # 2. Simulate Modulation Phase
            cond_to_drop_mod = rates['initial_cond'] - skip
            time_in_mod = cond_to_drop_mod / rates['mod_cond_rate'] if rates['mod_cond_rate'] > 0 else 0
            if time_in_mod < 0: time_in_mod = 0

            temp_at_skip = rates['initial_temp'] + (time_in_mod * rates['mod_temp_rate'])
            energy_mod = time_in_mod * rates['mod_energy_rate']

            # 3. Simulate Full Heat Phase
            cond_to_drop_full = skip - end
            time_in_full = cond_to_drop_full / rates['full_cond_rate'] if rates['full_cond_rate'] > 0 else 0

            temp_at_end = temp_at_skip + (time_in_full * rates['full_temp_rate'])
            energy_full = time_in_full * rates['full_energy_rate']

            # 4. Check Temp Safety Limit
            if temp_at_end > MAX_TEMP_C:
                status = f"TRIPPED {MAX_TEMP_C}°C"
                # If it trips, calculate the time it actually ran before tripping
                usable_time_in_full = (MAX_TEMP_C - temp_at_skip) / rates['full_temp_rate'] if rates[
                                                                                                   'full_temp_rate'] > 0 else 0
                total_time = time_in_mod + usable_time_in_full
                active_energy_kwh = (energy_mod + (usable_time_in_full * rates['full_energy_rate'])) / 1000.0
            else:
                status = "PASS"
                total_time = time_in_mod + time_in_full
                active_energy_kwh = (energy_mod + energy_full) / 1000.0

            # 5. Calculate CEF
            total_energy = active_energy_kwh + STANDBY_POWER_KWH
            cef = BONE_DRY_WEIGHT_LBS / total_energy if total_energy > 0 else 0

            # 6. Check Time Limit constraint
            if total_time <= MAX_TIME_MIN:
                results.append({
                    'Skip Thresh': skip,
                    'End Thresh': end,
                    'Sim RMC (%)': round(predicted_rmc, 2),
                    'Mod Time (m)': round(time_in_mod, 1),
                    'Full Time (m)': round(time_in_full if status == "PASS" else usable_time_in_full, 1),
                    'Total Time (m)': round(total_time, 1),
                    'Max Temp (°C)': round(min(temp_at_end, MAX_TEMP_C), 1),
                    'CEF': round(cef, 3),
                    'Status': status
                })

    df_results = pd.DataFrame(results)

    # Filter to only show runs that safely completed
    valid_runs = df_results[df_results['Status'] == "PASS"]

    if valid_runs.empty:
        print(
            f"WARNING: No combinations can achieve <= 2.0% RMC and <= {MAX_TIME_MIN}min without tripping {MAX_TEMP_C}°C first.")
    else:
        # Sort to prioritize finding the sweet spot: Close to 1.9% RMC to utilize time, and highest CEF
        valid_runs = valid_runs.sort_values(by=['Sim RMC (%)', 'CEF'], ascending=[False, False])
        print("=== OPTIMIZED THRESHOLDS (All criteria met: RMC <= 2.0%, Time <= 80m, Temp <= 53°C) ===")
        print(valid_runs.head(20).to_string(index=False))


if __name__ == "__main__":
    rmc_calibrator = train_rmc_model()
    extracted_rates = calculate_rates()
    simulate(extracted_rates, rmc_calibrator)