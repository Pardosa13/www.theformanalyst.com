"""
Betfair Auto-Mapping Helper

Provides fuzzy matching functionality to automatically map uploaded race meetings
to Betfair markets based on date, track, race number, and horse names.
"""

import os
import logging
import re
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Any

logger = logging.getLogger('betfair_mapper')

# Configuration
BETFAIR_ENABLED = os.environ.get('BETFAIR_ENABLED', 'false').lower() == 'true'
MIN_HORSE_MATCH_RATIO = 0.6  # At least 60% of horses must match
MIN_NAME_SIMILARITY = 0.8   # Name similarity threshold


def normalize_name(name: str) -> str:
    """Normalize a name for comparison (lowercase, remove special chars)"""
    if not name:
        return ''
    # Convert to lowercase and remove non-alphanumeric (keep spaces)
    normalized = re.sub(r'[^a-z0-9\s]', '', name.lower())
    # Remove extra spaces
    return ' '.join(normalized.split())


def fuzzy_match_score(name1: str, name2: str) -> float:
    """
    Calculate fuzzy match score between two names.
    Returns a score between 0.0 and 1.0.
    """
    n1 = normalize_name(name1)
    n2 = normalize_name(name2)
    
    if not n1 or not n2:
        return 0.0
    
    if n1 == n2:
        return 1.0
    
    # Try using fuzzywuzzy if available
    try:
        from fuzzywuzzy import fuzz
        # Use token_sort_ratio for best matching (handles word order differences)
        return fuzz.token_sort_ratio(n1, n2) / 100.0
    except ImportError:
        # Fallback to simple contains check
        if n1 in n2 or n2 in n1:
            return 0.9
        
        # Simple character overlap ratio
        set1 = set(n1.replace(' ', ''))
        set2 = set(n2.replace(' ', ''))
        if not set1 or not set2:
            return 0.0
        
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        return intersection / union if union > 0 else 0.0


def match_horse_names(
    our_horses: List[str],
    betfair_runners: List[Dict[str, Any]]
) -> Tuple[Dict[str, int], float]:
    """
    Match our horse names to Betfair runners.
    
    Args:
        our_horses: List of horse names from our database
        betfair_runners: List of runner dicts with 'runnerName' and 'selectionId'
    
    Returns:
        Tuple of (horse_to_selection_mapping, overall_confidence_score)
    """
    mapping = {}  # horse_name -> selection_id
    total_score = 0.0
    matched_count = 0
    
    for our_horse in our_horses:
        best_match_score = 0.0
        best_match_runner = None
        
        for runner in betfair_runners:
            runner_name = runner.get('runnerName', '')
            score = fuzzy_match_score(our_horse, runner_name)
            
            if score > best_match_score:
                best_match_score = score
                best_match_runner = runner
        
        if best_match_runner and best_match_score >= MIN_NAME_SIMILARITY:
            mapping[our_horse] = best_match_runner.get('selectionId')
            matched_count += 1
            total_score += best_match_score
    
    # Calculate overall confidence
    if not our_horses:
        return {}, 0.0
    
    # Confidence is based on:
    # 1. Percentage of horses matched
    # 2. Average match quality for matched horses
    match_ratio = matched_count / len(our_horses)
    avg_match_quality = total_score / matched_count if matched_count > 0 else 0.0
    
    # Combined confidence
    confidence = match_ratio * avg_match_quality
    
    return mapping, confidence


def find_best_market_match(
    track_name: str,
    race_number: int,
    race_date: Optional[datetime],
    horse_names: List[str],
    available_markets: List[Dict[str, Any]]
) -> Tuple[Optional[str], Dict[str, int], float]:
    """
    Find the best matching Betfair market for a race.
    
    Args:
        track_name: Name of the track/venue
        race_number: Race number
        race_date: Date of the race
        horse_names: List of horse names in the race
        available_markets: List of market dicts from Betfair API
    
    Returns:
        Tuple of (market_id, horse_to_selection_mapping, confidence_score)
    """
    best_market_id = None
    best_mapping = {}
    best_confidence = 0.0
    
    normalized_track = normalize_name(track_name)
    
    for market in available_markets:
        market_id = market.get('marketId')
        market_name = market.get('marketName', '')
        event_name = market.get('event', '') or ''
        venue = market.get('venue', '') or ''
        runners = market.get('runners', [])
        
        # Check if track/venue matches
        venue_match = False
        if normalized_track:
            venue_match = (
                fuzzy_match_score(track_name, venue) >= 0.7 or
                fuzzy_match_score(track_name, event_name) >= 0.7
            )
        
        # Check if race number is in market name (e.g., "R1", "Race 1")
        race_match = False
        race_patterns = [
            f'r{race_number}',
            f'race {race_number}',
            f'race{race_number}'
        ]
        market_name_lower = market_name.lower()
        for pattern in race_patterns:
            if pattern in market_name_lower:
                race_match = True
                break
        
        # Match horse names
        horse_mapping, horse_confidence = match_horse_names(horse_names, runners)
        
        # Calculate overall confidence
        # Weight: horse matching is most important (60%), venue (25%), race number (15%)
        confidence = 0.0
        
        if horse_confidence > 0:
            confidence += horse_confidence * 0.6
        
        if venue_match:
            confidence += 0.25
        
        if race_match:
            confidence += 0.15
        
        # Only consider if we have reasonable horse matches
        if horse_confidence >= MIN_HORSE_MATCH_RATIO and confidence > best_confidence:
            best_market_id = market_id
            best_mapping = horse_mapping
            best_confidence = confidence
    
    return best_market_id, best_mapping, best_confidence


def auto_map_race(race_obj, available_markets: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """
    Attempt to automatically map a race to a Betfair market.
    
    Args:
        race_obj: Race SQLAlchemy object
        available_markets: List of market dicts from Betfair API
    
    Returns:
        Tuple of (success, message)
    """
    if not BETFAIR_ENABLED:
        return False, "Betfair integration not enabled"
    
    if race_obj.betfair_market_id:
        return False, "Race already mapped"
    
    # Get track name from meeting
    track_name = race_obj.meeting.track or race_obj.meeting.meeting_name
    race_number = race_obj.race_number
    race_date = race_obj.meeting.date
    
    # Get horse names
    horse_names = [horse.horse_name for horse in race_obj.horses]
    
    if not horse_names:
        return False, "No horses in race"
    
    # Find best match
    market_id, horse_mapping, confidence = find_best_market_match(
        track_name, race_number, race_date, horse_names, available_markets
    )
    
    if not market_id:
        return False, "No matching market found"
    
    if confidence < MIN_HORSE_MATCH_RATIO:
        return False, f"Low confidence match ({confidence:.0%})"
    
    # Apply mapping
    from datetime import datetime as dt
    race_obj.betfair_market_id = market_id
    race_obj.betfair_mapping_confidence = confidence
    race_obj.betfair_mapped_at = dt.utcnow()
    
    # Map individual horses
    for horse in race_obj.horses:
        if horse.horse_name in horse_mapping:
            horse.betfair_selection_id = horse_mapping[horse.horse_name]
    
    logger.info(f"Auto-mapped Race {race_number} ({track_name}) to market {market_id} "
                f"with {confidence:.0%} confidence")
    
    return True, f"Mapped to {market_id} ({confidence:.0%} confidence)"


def run_sanity_checks() -> List[str]:
    """Run sanity checks on the mapping logic"""
    issues = []
    
    # Test normalize_name
    test_cases = [
        ("HORSE NAME", "horse name"),
        ("Horse (AUS)", "horse aus"),
        ("THE  HORSE", "the horse"),
    ]
    
    for input_name, expected in test_cases:
        result = normalize_name(input_name)
        if result != expected:
            issues.append(f"normalize_name('{input_name}') = '{result}', expected '{expected}'")
    
    # Test fuzzy_match_score
    match_cases = [
        ("Horse", "Horse", 1.0),
        ("Horse", "HORSE", 1.0),
        ("Horse", "Hors", 0.7),  # Should be > 0.7
    ]
    
    for name1, name2, min_expected in match_cases:
        score = fuzzy_match_score(name1, name2)
        if score < min_expected:
            issues.append(f"fuzzy_match_score('{name1}', '{name2}') = {score:.2f}, expected >= {min_expected}")
    
    if not issues:
        logger.info("All sanity checks passed")
    else:
        for issue in issues:
            logger.warning(f"Sanity check failed: {issue}")
    
    return issues


# Run sanity checks on module import (in debug mode)
if os.environ.get('DEBUG', 'false').lower() == 'true':
    run_sanity_checks()
