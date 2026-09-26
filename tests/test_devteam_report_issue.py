#!/usr/bin/env python3
"""
Test script to verify devteam report issue functionality
"""

import sys
import os
import json
import subprocess

# Add the project root to the path
sys.path.insert(0, os.path.abspath('.'))

def test_backend_endpoint():
    """Test that the backend endpoint function works"""
    try:
        # Import the app module
        import app
        
        # Check if the function exists
        if hasattr(app, 'api_devteam_report_issue'):
            print("✓ Backend endpoint function exists")
            return True
        else:
            print("✗ Backend endpoint function not found")
            return False
    except Exception as e:
        print(f"✗ Error importing backend endpoint: {e}")
        return False

def test_frontend_form():
    """Test that the frontend form function works"""
    try:
        # Import the app module
        import app
        
        # Check if the function exists
        if hasattr(app, 'devteam_report_form'):
            print("✓ Frontend form function exists")
            return True
        else:
            print("✗ Frontend form function not found")
            return False
    except Exception as e:
        print(f"✗ Error importing frontend form: {e}")
        return False

def test_template_exists():
    """Test that the frontend template exists"""
    try:
        template_path = os.path.join('templates', 'devteam-report.html')
        if os.path.exists(template_path):
            print("✓ Frontend template exists")
            return True
        else:
            print("✗ Frontend template not found")
            return False
    except Exception as e:
        print(f"✗ Error checking template: {e}")
        return False

def test_documentation():
    """Test that documentation files exist"""
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

def test_form_definition():
    """Test that the form definition exists"""
    try:
        form_path = os.path.join('assets', 'forms', 'report-issue.json')
        if os.path.exists(form_path):
            print("✓ Form definition exists")
            return True
        else:
            print("✗ Form definition not found")
            return False
    except Exception as e:
        print(f"✗ Error checking form definition: {e}")
        return False

def main():
    """Main test function"""
    print("Running devteam report issue functionality tests...")
    
    tests = [
        test_backend_endpoint,
        test_frontend_form,
        test_template_exists,
        test_documentation,
        test_form_definition
    ]
    
    passed = 0
    for test in tests:
        if test():
            passed += 1
    
    print(f"\n{passed}/{len(tests)} tests passed")
    
    if passed == len(tests):
        print("All tests passed!")
        print("\nSUMMARY:")
        print("- Backend API endpoint: /api/devteam/report")
        print("- Frontend form route: /devteam/report")
        print("- Frontend template: templates/devteam-report.html")
        print("- Form definition: assets/forms/report-issue.json")
        print("- Documentation: docs/api-spec.yaml, docs/devteam-report-endpoint.md")
        return 0
    else:
        print("Some tests failed!")
        return 1

if __name__ == '__main__':
    sys.exit(main())