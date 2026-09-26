#!/usr/bin/env python3
"""
Test script for devteam endpoints
"""

import sys
import os

# Add the project root to the path
sys.path.insert(0, os.path.abspath('.'))

def test_imports():
    """Test that we can import the app module"""
    try:
        import app
        print("✓ Successfully imported app module")
        return True
    except Exception as e:
        print(f"✗ Failed to import app module: {e}")
        return False

def test_endpoint_exists():
    """Test that the devteam report endpoint function exists"""
    try:
        import app
        # Check if the function exists
        if hasattr(app, 'api_devteam_report_issue'):
            print("✓ api_devteam_report_issue function exists")
            return True
        else:
            print("✗ api_devteam_report_issue function not found")
            return False
    except Exception as e:
        print(f"✗ Error checking endpoint: {e}")
        return False

def test_form_endpoint_exists():
    """Test that the devteam report form endpoint function exists"""
    try:
        import app
        # Check if the function exists
        if hasattr(app, 'devteam_report_form'):
            print("✓ devteam_report_form function exists")
            return True
        else:
            print("✗ devteam_report_form function not found")
            return False
    except Exception as e:
        print(f"✗ Error checking form endpoint: {e}")
        return False

def main():
    """Main test function"""
    print("Running devteam endpoints tests...")
    
    tests = [
        test_imports,
        test_endpoint_exists,
        test_form_endpoint_exists
    ]
    
    passed = 0
    for test in tests:
        if test():
            passed += 1
    
    print(f"\n{passed}/{len(tests)} tests passed")
    
    if passed == len(tests):
        print("All tests passed!")
        return 0
    else:
        print("Some tests failed!")
        return 1

if __name__ == '__main__':
    sys.exit(main())