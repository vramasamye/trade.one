#!/usr/bin/env python3
"""
Test script to debug growwapi imports
"""

try:
    print("Testing growwapi imports...")
    
    # Test basic import
    import growwapi
    print(f"✓ growwapi module imported successfully")
    print(f"  Module location: {growwapi.__file__ if hasattr(growwapi, '__file__') else 'unknown'}")
    print(f"  Module attributes: {dir(growwapi)}")
    
    # Test specific imports
    try:
        from growwapi import GrowwAPI
        print("✓ GrowwAPI imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import GrowwAPI: {e}")
    
    try:
        from growwapi import GrowwFeed
        print("✓ GrowwFeed imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import GrowwFeed: {e}")
        
    # Check version if available
    if hasattr(growwapi, '__version__'):
        print(f"  Package version: {growwapi.__version__}")
    
except ImportError as e:
    print(f"✗ Failed to import growwapi module: {e}")
except Exception as e:
    print(f"✗ Unexpected error: {e}")

print("\nDone.")