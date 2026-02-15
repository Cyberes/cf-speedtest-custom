"""Argument parser options for speedtest"""


def add_run_options(parser):
    """
    Add speedtest-specific command line options to an argument parser.
    
    Args:
        parser: argparse.ArgumentParser instance
    
    Returns:
        The parser with added options
    """
    parser.add_argument(
        '--percentile',
        type=float,
        default=90,
        help='Percentile to use for bandwidth calculation (0-100, default: 90)'
    )
    parser.add_argument(
        '--testpatience',
        type=int,
        default=15,
        help='Test patience timeout in seconds (default: 15)'
    )
    return parser
