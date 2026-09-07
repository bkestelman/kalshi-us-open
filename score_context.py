"""Interpret cached scores by exchange competitor ID, never ticker initials."""
import time


def confirmed_winner(score, best_of):
    """Return a mapped winner from a final result, including pre-close ended data.

    The feed reports status=ended with a complete score roughly two minutes
    before status=closed. Require a normal completed best-of-three/five score
    for that early result. Closed results also cover retirements.
    """
    ids = [score.get('competitor1_id'), score.get('competitor2_id')]
    winner = score.get('winner')
    if not all(ids) or ids[0] == ids[1] or winner not in ids:
        return None
    if score.get('match_status') != 'ended':
        return None
    if score.get('status') == 'closed':
        return winner
    if score.get('status') != 'ended':
        return None
    try:
        best_of = int(best_of)
        sets = [int(score[f'competitor{i}_overall_score']) for i in (1, 2)]
    except (ValueError, TypeError, KeyError):
        return None
    if best_of not in (3, 5):
        return None
    target = best_of // 2 + 1
    winner_index = ids.index(winner)
    if sets[winner_index] == target and 0 <= sets[1 - winner_index] < target:
        return winner
    return None


def future_round(round_name, ticker):
    """A loss in this round must make the target qualification impossible."""
    if 'ADVANCE-' not in ticker:
        return True
    current = {'round of 128': 128, 'round of 64': 64, 'round of 32': 32,
               'round of 16': 16, 'quarterfinal': 8, 'quarterfinals': 8,
               'semifinal': 4, 'semifinals': 4, 'final': 2}.get((round_name or '').lower())
    target = next((n for label, n in [('QUAR', 8), ('SEMI', 4), ('FIN', 2)]
                   if label in ticker.rsplit('-', 1)[0]), None)
    return current is not None and target is not None and current > target


def context(matches, match_ticker, now=None):
    now = time.time() if now is None else now
    event = match_ticker.rsplit('-', 1)[0]
    snapshot = matches.get(event)
    if not snapshot:
        return {'state': 'missing'}
    player = snapshot.get('players', {}).get(match_ticker, {}).get('id')
    score = snapshot.get('score') or {}
    ids = [score.get('competitor1_id'), score.get('competitor2_id')]
    if not player or player not in ids or not all(ids) or ids[0] == ids[1]:
        return {'state': 'identity-mismatch'}
    index = ids.index(player) + 1
    opponent = 3 - index
    result = {'state': 'unconfirmed', 'received_age_s': round(now - snapshot['received_at'], 3),
              'milestone_id': snapshot['milestone_id'], 'player_id': player,
              'status': score.get('status'), 'match_status': score.get('match_status'),
              'best_of': snapshot.get('best_of'), 'round': snapshot.get('round'),
              'sets': score.get(f'competitor{index}_overall_score'),
              'opponent_sets': score.get(f'competitor{opponent}_overall_score'),
              'games': score.get(f'competitor{index}_round_scores'),
              'opponent_games': score.get(f'competitor{opponent}_round_scores'),
              'points': score.get(f'competitor{index}_current_round_score'),
              'opponent_points': score.get(f'competitor{opponent}_current_round_score')}
    # A just-fetched response can contain stale provider data. Receiving it
    # recently is necessary for use, but does not prove the score is current.
    if not 0 <= result['received_age_s'] <= 90:
        result['state'] = 'stale'
        return result
    winner = confirmed_winner(score, snapshot.get('best_of'))
    if winner:
        result['state'] = 'won' if winner == player else 'lost'
    return result
