"""Utility functions for speedtest calculations"""


def percentile(values, perc=0.5):
    """
    Calculate percentile from a list of values.
    
    Args:
        values: List of numeric values
        perc: Percentile value between 0 and 1, or 0-100 (default: 0.5 for median)
              If > 1, assumes 0-100 range and converts to 0-1
    
    Returns:
        The calculated percentile value
    """
    if not values:
        return 0
    
    # Convert from 0-100 range to 0-1 if needed
    if perc > 1:
        perc = perc / 100.0
    
    sorted_vals = sorted(values)
    idx = (len(sorted_vals) - 1) * perc
    rem = idx % 1
    
    if rem == 0:
        return sorted_vals[int(round(idx))]
    
    # Calculate weighted average
    floor_idx = int(idx)
    ceil_idx = int(idx) + 1
    edges = [sorted_vals[floor_idx], sorted_vals[ceil_idx]]
    return edges[0] + (edges[1] - edges[0]) * rem
