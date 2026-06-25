"""
Boore-Atkinson 2008 NGA GMPE + Caracas Basin Amplification.

Coefficients from Boore & Atkinson (2008) Earthquake Spectra 24(1), Tables 6-8.
PGA, strike-slip mechanism.
"""

import math

# BA08 coefficients (PGA, strike-slip)
BA08 = {
    'e2': -0.50350, 'e5': 0.28805, 'e6': -0.10164, 'e7': 0.0,
    'Mh': 6.75,
    'c1': -0.66050, 'c2': 0.11970, 'c3': -0.01151,
    'Mref': 4.5, 'Rref': 1.0, 'h': 1.35,
    'blin': -0.360, 'b1': -0.640, 'b2': -0.14,
    'Vref': 760, 'V1': 180, 'V2': 300,
    'a1': 0.03, 'a2': 0.09, 'pga_low': 0.06,
    'sigma': 0.5653,
}

BASIN_AMP = 1.5   # Extra basin resonance beyond BA08 Vs30=270 response
BASIN_VS30 = 270   # Caracas Valley average Vs30

CARACAS_LAT = 10.4806
CARACAS_LON = -66.9036


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in km between two points on Earth."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def ba08_pga_gal(mag: float, rjb: float, vs30: float = BASIN_VS30) -> dict:
    """
    Compute PGA using Boore-Atkinson 2008.

    Args:
        mag: Moment magnitude
        rjb: Joyner-Boore distance (km)
        vs30: Shear-wave velocity (m/s), default Caracas basin

    Returns:
        dict with 'rock' (gal), 'soil' (gal), 'soil84' (84th percentile, gal)
    """
    B = BA08

    # Source term (strike-slip: e2)
    if mag <= B['Mh']:
        FM = B['e2'] + B['e5'] * (mag - B['Mh']) + B['e6'] * (mag - B['Mh']) ** 2
    else:
        FM = B['e2'] + B['e7'] * (mag - B['Mh'])

    # Distance term
    R = math.sqrt(rjb ** 2 + B['h'] ** 2)
    FD = ((B['c1'] + B['c2'] * (mag - B['Mref'])) * math.log(R / B['Rref']) +
          B['c3'] * (R - B['Rref']))

    # Rock PGA (Vs30=760)
    ln_pga_rock = FM + FD
    pga_rock_g = math.exp(ln_pga_rock)

    # Site term — linear
    FLIN = B['blin'] * math.log(vs30 / B['Vref'])

    # Site term — nonlinear
    if vs30 <= B['V1']:
        bnl = B['b1']
    elif vs30 <= B['V2']:
        bnl = ((B['b1'] - B['b2']) * math.log(vs30 / B['V2']) /
               math.log(B['V1'] / B['V2']) + B['b2'])
    elif vs30 <= B['Vref']:
        bnl = B['b2'] * math.log(vs30 / B['Vref']) / math.log(B['V2'] / B['Vref'])
    else:
        bnl = 0.0

    if pga_rock_g <= B['a1']:
        FNL = bnl * math.log(B['pga_low'] / 0.1)
    elif pga_rock_g <= B['a2']:
        FNL = bnl * math.log(B['pga_low'] / 0.1)
    else:
        FNL = bnl * math.log(pga_rock_g / 0.1)

    ln_pga = FM + FD + FLIN + FNL
    pga_g = math.exp(ln_pga)

    return {
        'rock': pga_rock_g * 980.665,
        'soil': pga_g * BASIN_AMP * 980.665,
        'soil84': math.exp(ln_pga + B['sigma']) * BASIN_AMP * 980.665,
    }


def pga_to_mmi(gal: float) -> float:
    """Convert PGA (gal) to MMI using Worden 2012."""
    if gal <= 0:
        return 1.0
    lg = math.log10(gal)
    raw = 1.78 + 1.55 * lg if lg <= 1.57 else -1.60 + 3.70 * lg
    return round(max(1.0, min(12.0, raw)), 1)


MMI_DESC = {
    1: "Not felt", 2: "Weak", 3: "Weak", 4: "Light",
    5: "Moderate", 6: "Strong", 7: "Very Strong",
    8: "Severe", 9: "Violent", 10: "Extreme",
}


def mmi_description(mmi: float) -> str:
    """Human-readable MMI description."""
    return MMI_DESC.get(min(max(round(mmi), 1), 10), "Extreme")


def swave_seconds(dist_km: float, depth_km: float) -> float:
    """Estimate S-wave travel time in seconds (avg Vs ~3.5 km/s)."""
    hypo = math.sqrt(dist_km ** 2 + depth_km ** 2)
    return hypo / 3.5


def caracas_impact(mag: float, lat: float, lon: float, depth_km: float) -> dict:
    """
    Full Caracas impact assessment for an earthquake.

    Returns dict with: dist_km, pga_rock, pga_soil, pga_soil84, mmi, mmi_desc,
                        swave_s, alarm_level
    """
    dist_km = haversine(CARACAS_LAT, CARACAS_LON, lat, lon)
    rjb = max(0.1, dist_km)  # Approximate Rjb ~ epicentral distance

    pga = ba08_pga_gal(mag, rjb)
    mmi = pga_to_mmi(pga['soil'])

    return {
        'dist_km': round(dist_km, 1),
        'pga_rock': round(pga['rock'], 2),
        'pga_soil': round(pga['soil'], 2),
        'pga_soil84': round(pga['soil84'], 2),
        'mmi': mmi,
        'mmi_desc': mmi_description(mmi),
        'swave_s': round(swave_seconds(dist_km, depth_km), 1),
        'alarm_level': alarm_level(mag, dist_km),
    }


def alarm_level(mag: float, dist_km: float) -> str:
    """Classify alarm severity based on magnitude and distance to Caracas."""
    if mag >= 6.0 and dist_km < 400:
        return 'critical'
    if mag >= 5.0 and dist_km < 300:
        return 'high'
    if mag >= 4.5 and dist_km < 300:
        return 'medium'
    if mag >= 4.0 and dist_km < 500:
        return 'low'
    if mag >= 3.0 and dist_km < 200:
        return 'info'
    return 'none'
