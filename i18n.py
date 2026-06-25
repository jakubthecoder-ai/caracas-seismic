"""
Server-side internationalization for Seismic Monitor.

ES (Spanish) is the default language. EN (English) is the alternative.
Used by Jinja2 templates and SSE alarm messages.
"""

TRANSLATIONS = {
    'es': {
        'title': 'MONITOR SÍSMICO CARACAS',
        'connecting': 'CONECTANDO...',
        'no_threat': 'SIN AMENAZA SÍSMICA',
        'status_monitoring': 'MONITOREO ACTIVO — Sin amenaza inmediata detectada',
        'show_details': 'Mostrar detalles',
        'hide_details': 'Ocultar detalles',
        'last_event': 'Último evento',
        'no_events': 'Sin eventos recientes',
        'events': 'EVENTOS',
        'waveforms': 'ONDAS',
        'impact_title': 'IMPACTO CARACAS',
        'threat_level': 'Nivel de Amenaza Actual',
        'last_sig': 'Último evento significativo:',
        'time_ago': 'Hace:',
        'est_pga': 'PGA est. Caracas (cuenca):',
        'est_mmi': 'MMI est. Caracas:',
        'swave_arrival': 'Llegada onda S:',
        'events_24h': 'Eventos (24h, región)',
        'seismograms': 'SISMOGRAMAS EN VIVO',
        'sound_on': 'Sonido ON',
        'sound_off': 'Sonido OFF',
        'enable_notif': 'Habilitar Notificaciones',
        'notif_on': 'Notif ON',
        'notif_off': 'Notif OFF',
        'alert_critical': 'ALERTA CRÍTICA — Posible sacudida fuerte en Caracas — M{mag} — {place}',
        'alert_high': 'ALERTA SÍSMICA — Posible sacudida moderada en Caracas — M{mag} — {place}',
        'alert_medium': 'ACTIVIDAD SÍSMICA — M{mag} — {place}',
        'alert_waveform': 'MOVIMIENTO SÍSMICO DETECTADO EN {station}',
        'alert_waveform_warning': 'MOVIMIENTO SÍSMICO DETECTADO — Se monitorea la situación',
        'alert_waveform_critical': 'POSIBLE TERREMOTO EN CURSO — Busque protección',
        'alert_spike': 'MOVIMIENTO SÍSMICO FUERTE — Busque protección',
        'monitor_target': 'Su objetivo de monitoreo',
        'severe_zone': '200 km — Zona de sacudida severa',
        'moderate_zone': '400 km — Zona de sacudida moderada',
        'san_sebastian': 'Falla de San Sebastián',
        'seedlink_station': 'Estación SeedLink',
        'radius': 'Radio',
        'rings': {8:'VIII Severo',7:'VII Muy Fuerte',6:'VI Fuerte',5:'V Moderado',4:'IV Leve',3:'III Débil',2:'II Apenas sentido'},
        'notif_title': 'Terremoto M{mag} cerca de Caracas',
        'depth': 'Prof.',
        'dist_caracas': 'Dist Caracas',
        'pga_basin': 'PGA cuenca',
        'felt': 'SENTIDO',
        'not_felt': 'no sentido',
        'stations_label': 'Estaciones',
        'offline': 'DESCONECTADO',
        'connected': 'CONECTADO',
        'tsunami_banner': 'TSUNAMI {severity}: {title}',
        'tsunami_notif': 'ALERTA TSUNAMI — Posible amenaza costera para Caracas. Busque terreno alto si está cerca de la costa.',
        'mmi': {
            1: 'No sentido', 2: 'Débil', 3: 'Débil', 4: 'Leve',
            5: 'Moderado', 6: 'Fuerte', 7: 'Muy Fuerte', 8: 'Severo',
            9: 'Violento', 10: 'Extremo',
        },
        'lang_switch': 'EN',
        'lang_switch_title': 'English',
        'ago_s': 'hace {n}s',
        'ago_m': 'hace {n}m',
        'ago_h': 'hace {n}h',
        'ago_d': 'hace {n}d',
    },
    'en': {
        'title': 'CARACAS SEISMIC MONITOR',
        'connecting': 'CONNECTING...',
        'no_threat': 'NO SEISMIC THREAT',
        'status_monitoring': 'MONITORING ACTIVE — No immediate threat detected',
        'show_details': 'Show details',
        'hide_details': 'Hide details',
        'last_event': 'Last event',
        'no_events': 'No recent events',
        'events': 'EVENTS',
        'waveforms': 'WAVEFORMS',
        'impact_title': 'CARACAS IMPACT',
        'threat_level': 'Current Threat Level',
        'last_sig': 'Last significant event:',
        'time_ago': 'Time ago:',
        'est_pga': 'Est. PGA Caracas (basin):',
        'est_mmi': 'Est. MMI Caracas:',
        'swave_arrival': 'S-wave arrival:',
        'events_24h': 'Events (24h, region)',
        'seismograms': 'LIVE SEISMOGRAMS',
        'sound_on': 'Sound ON',
        'sound_off': 'Sound OFF',
        'enable_notif': 'Enable Notifications',
        'notif_on': 'Notif ON',
        'notif_off': 'Notif OFF',
        'alert_critical': 'CRITICAL ALERT — Strong shaking possible in Caracas — M{mag} — {place}',
        'alert_high': 'SEISMIC ALERT — Moderate shaking possible in Caracas — M{mag} — {place}',
        'alert_medium': 'SEISMIC ACTIVITY — M{mag} — {place}',
        'alert_waveform': 'SEISMIC MOVEMENT DETECTED AT {station}',
        'alert_waveform_warning': 'SEISMIC MOVEMENT DETECTED — Monitoring active',
        'alert_waveform_critical': 'POSSIBLE EARTHQUAKE IN PROGRESS — Seek cover',
        'alert_spike': 'STRONG SEISMIC MOVEMENT — Seek cover',
        'monitor_target': 'Your monitoring target',
        'severe_zone': '200 km — Severe shaking zone',
        'moderate_zone': '400 km — Moderate shaking zone',
        'san_sebastian': 'San Sebastian Fault',
        'seedlink_station': 'SeedLink station',
        'radius': 'Radius',
        'rings': {8:'VIII Severe',7:'VII Very Strong',6:'VI Strong',5:'V Moderate',4:'IV Light',3:'III Weak',2:'II Barely felt'},
        'notif_title': 'Earthquake M{mag} near Caracas',
        'depth': 'Depth',
        'dist_caracas': 'Dist Caracas',
        'pga_basin': 'PGA basin',
        'felt': 'FELT',
        'not_felt': 'not felt',
        'stations_label': 'Stations',
        'offline': 'OFFLINE',
        'connected': 'CONNECTED',
        'tsunami_banner': 'TSUNAMI {severity}: {title}',
        'tsunami_notif': 'TSUNAMI ALERT — Possible coastal threat to Caracas. Seek high ground if near coast.',
        'mmi': {
            1: 'Not felt', 2: 'Weak', 3: 'Weak', 4: 'Light',
            5: 'Moderate', 6: 'Strong', 7: 'Very Strong', 8: 'Severe',
            9: 'Violent', 10: 'Extreme',
        },
        'lang_switch': 'ES',
        'lang_switch_title': 'Español',
        'ago_s': '{n}s ago',
        'ago_m': '{n}m ago',
        'ago_h': '{n}h ago',
        'ago_d': '{n}d ago',
    },
}


def t(key, lang='es', **kwargs):
    """Get translated string. Supports format kwargs."""
    strings = TRANSLATIONS.get(lang, TRANSLATIONS['es'])
    text = strings.get(key)
    if text is None:
        text = TRANSLATIONS['es'].get(key, key)
    if isinstance(text, dict):
        return text
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


def mmi_desc(mmi_value, lang='es'):
    """Get MMI description for a given intensity value."""
    mmi_dict = TRANSLATIONS.get(lang, TRANSLATIONS['es']).get('mmi', {})
    rounded = round(mmi_value)
    return mmi_dict.get(min(rounded, 10), mmi_dict.get(10, 'Extreme'))


def time_ago(seconds, lang='es'):
    """Format seconds ago into human-readable string."""
    strings = TRANSLATIONS.get(lang, TRANSLATIONS['es'])
    if seconds < 60:
        return strings['ago_s'].format(n=int(seconds))
    if seconds < 3600:
        return strings['ago_m'].format(n=int(seconds / 60))
    if seconds < 86400:
        return strings['ago_h'].format(n=int(seconds / 3600))
    return strings['ago_d'].format(n=int(seconds / 86400))
