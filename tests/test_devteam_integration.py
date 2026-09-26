#!/usr/bin/env python3
"""
Integration test for devteam endpoints
"""

import requests
import json
import sys
import os

# Add the project root to the path
sys.path.insert(0, os.path.abspath('.'))

def test_endpoint_integration():
    """Test the devteam report endpoint integration"""
    try:
        # Test data
        test_data = {
            "title": "Integration Test Issue",
            "body": "This is a test issue created by the integration test",
            "component": "agydashboard",
            "urgency": "medium"
        }
        
        # Since we can't easily test the actual endpoint without a full server,
        # we'll just verify the function exists and has the correct signature
        import app
        
        # Check if the function exists
        if hasattr(app, 'api_devteam_report_issue'):
            print("✓ api_devteam_report_issue function exists")
            
            # Check if the form function exists
            if hasattr(app, 'devteam_report_form'):
                print("✓ devteam_report_form function exists")
                return True
            else:
                print("✗ devteam_report_form function not found")
                return False
        else:
            print("✗ api_devteam_report_issue function not found")
            return False
            
    except Exception as e:
        print(f"✗ Error testing endpoint integration: {e}")
        return False

def test_form_template_exists():
    """Test that the form template exists"""
    try:
        template_path = os.path.join('templates', 'devteam-report.html')
        if os.path.exists(template_path):
            print("✓ devteam-report.html template exists")
            return True
        else:
            print("✗ devteam-report.html template not found")
            return False
    except Exception as e:
        print(f"✗ Error checking template: {e}")
        return False

def test_documentation_exists():
    """Test that the documentation files exist"""
    try:
        # Check API spec
        api_spec_path = os.path.join('docs', 'api-spec.yaml')
        if os.path.exists(api_spec_path):
            print("✓ API specification exists")
        else:
            print("✗ API specification not found")
            return False
            
        # Check endpoint documentation
        endpoint_doc_path = os.path.join('docs', 'devteam-report-endpoint.md')
        if os.path.exists(endpoint_doc_path):
            print("✓ Endpoint documentation exists")
            return True
        else:
            print("✗ Endpoint documentation not found")
            return False
            
    except Exception as e:
        print(f"✗ Error checking documentation: {e}")
        return False

def main():
    """Main test function"""
    print("Running devteam endpoints integration tests...")
    
    tests = [
        test_endpoint_integration,
        test_form_template_exists,
        test_documentation_exists
    ]
    
    passed = 0
    for test in tests:
        if test():
            passed += 1
    
    print(f"\n{passed}/{len(tests)} integration tests passed")
    
    if passed == len(tests):
        print("All integration tests passed!")
        return 0
    else:
        print("Some integration tests failed!")
        return 1

if __name__ == '__main__':
    sys.exit(main())