"""Share primary discovery mappings with paper comparisons, without API scans."""
import json
import time


def export_groups(groups, discovery):
    rows = []
    for (tour, key), group in groups.items():
        players = {}
        for leg in group['legs']:
            p = players.setdefault(leg.code, {'match': leg.match_tk,
                                              'name': leg.name, 'legs': []})
            p['legs'].append([leg.win_tk, leg.win_event])
        if group['legs']:
            rows.append([tour, key, group['legs'][0].comp, players])
    return {'updated_at': time.time(), 'groups': rows,
            'started': [list(k) for k in discovery.started],
            'schedule': discovery.schedule()}


class FollowerDiscovery:
    def __init__(self, path):
        self.path = path
        self.started, self.closed = set(), set()
        self.refreshed = []
        self.note = 'waiting for primary discovery'
        self._schedule = {}

    def schedule(self):
        return self._schedule

    def scan(self, live_keys):
        with open(self.path) as f:
            data = json.load(f)
        age = time.time() - data['updated_at']
        if not 0 <= age <= 180:
            raise ValueError(f'primary discovery snapshot is {age:.1f}s old')
        groups = data['groups']
        current = {(row[0], row[1]) for row in groups}
        self.started = {tuple(k) for k in data['started']}
        self.closed = set(live_keys) - current
        self.refreshed = [r for r in groups if (r[0], r[1]) in live_keys]
        self._schedule = data['schedule']
        newly = [r for r in groups if (r[0], r[1]) not in live_keys]
        self.note = f'primary discovery, {len(current)} matches, snapshot age {age:.1f}s'
        return newly, current
