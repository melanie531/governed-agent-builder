"""CloudFormation for a private-S3 frontend and an isolated private HTTP origin.

This does not provision EC2, certificates or DNS. The backend provisioning layer
must be completed first. Private HTTP approved 2026-09-11; viewer and Cognito remain HTTPS.
"""
import argparse
import json


def template():
    tags = [{"Key": k, "Value": v} for k, v in {"project": "governed-agent-builder", "owner": "melanie531", "managedBy": "cloudformation"}.items()]
    disabled_cache = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    # AllViewer forwards Authorization and Host; the private proxy must validate the
    # configured distribution Host and set only its known origin for the app.
    all_viewer = "216adef6-5c7f-47e4-b989-5492eafa07d3"
    r = {
        "WebBucket": {"Type": "AWS::S3::Bucket", "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain", "Properties": {
            "Tags": tags, "BucketEncryption": {"ServerSideEncryptionConfiguration": [{"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]},
            "PublicAccessBlockConfiguration": {"BlockPublicAcls": True, "BlockPublicPolicy": True, "IgnorePublicAcls": True, "RestrictPublicBuckets": True},
            "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}, "VersioningConfiguration": {"Status": "Enabled"},
        }},
        "OAC": {"Type": "AWS::CloudFront::OriginAccessControl", "Properties": {"OriginAccessControlConfig": {
            "Name": "governed-agent-builder-web", "OriginAccessControlOriginType": "s3", "SigningBehavior": "always", "SigningProtocol": "sigv4",
        }}},
        "VpcOrigin": {"Type": "AWS::CloudFront::VpcOrigin", "Properties": {"Tags": tags, "VpcOriginEndpointConfig": {
            "Name": "governed-agent-builder-backend", "Arn": {"Ref": "PrivateOriginArn"}, "HTTPPort": 80, "HTTPSPort": 443,
            "OriginProtocolPolicy": "http-only", "OriginSSLProtocols": ["TLSv1.2"],
        }}},
        "SpaRewrite": {"Type": "AWS::CloudFront::Function", "Properties": {
            "Name": "governed-agent-builder-spa", "AutoPublish": True,
            "FunctionConfig": {"Comment": "Rewrite frontend routes only; never API or auth responses", "Runtime": "cloudfront-js-2.0"},
            "FunctionCode": "function handler(event) { var r=event.request; if (r.uri.indexOf('.') === -1) { r.uri='/index.html'; } return r; }",
        }},
        "SecurityHeaders": {"Type": "AWS::CloudFront::ResponseHeadersPolicy", "Properties": {"ResponseHeadersPolicyConfig": {
            "Name": "governed-agent-builder-security", "SecurityHeadersConfig": {
                "StrictTransportSecurity": {"AccessControlMaxAgeSec": 31536000, "IncludeSubdomains": True, "Override": True},
                "ContentTypeOptions": {"Override": True}, "FrameOptions": {"FrameOption": "DENY", "Override": True},
                "ReferrerPolicy": {"ReferrerPolicy": "no-referrer", "Override": True},
                "ContentSecurityPolicy": {"Override": True, "ContentSecurityPolicy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"},
            },
        }}},
    }
    api_behavior = {"TargetOriginId": "backend", "ViewerProtocolPolicy": "https-only", "AllowedMethods": ["GET", "HEAD", "OPTIONS", "PUT", "PATCH", "POST", "DELETE"],
                    "CachedMethods": ["GET", "HEAD"], "CachePolicyId": disabled_cache, "OriginRequestPolicyId": all_viewer,
                    "ResponseHeadersPolicyId": {"Ref": "SecurityHeaders"}, "Compress": True}
    r["Distribution"] = {"Type": "AWS::CloudFront::Distribution", "Properties": {"Tags": tags, "DistributionConfig": {
        "Enabled": True, "Comment": "governed-agent-builder Agent Studio", "DefaultRootObject": "index.html", "PriceClass": "PriceClass_100",
        "HttpVersion": "http2and3", "IPV6Enabled": True, "ViewerCertificate": {"CloudFrontDefaultCertificate": True},
        "Origins": [
            {"Id": "web", "DomainName": {"Fn::GetAtt": ["WebBucket", "RegionalDomainName"]}, "S3OriginConfig": {"OriginAccessIdentity": ""}, "OriginAccessControlId": {"Fn::GetAtt": ["OAC", "Id"]}},
            {"Id": "backend", "DomainName": {"Ref": "PrivateOriginHostname"}, "VpcOriginConfig": {"VpcOriginId": {"Fn::GetAtt": ["VpcOrigin", "Id"]}, "OriginReadTimeout": 30}},
        ],
        "DefaultCacheBehavior": {"TargetOriginId": "web", "ViewerProtocolPolicy": "redirect-to-https", "AllowedMethods": ["GET", "HEAD", "OPTIONS"], "CachedMethods": ["GET", "HEAD"],
            "CachePolicyId": disabled_cache, "Compress": True, "ResponseHeadersPolicyId": {"Ref": "SecurityHeaders"},
            "FunctionAssociations": [{"EventType": "viewer-request", "FunctionARN": {"Fn::GetAtt": ["SpaRewrite", "FunctionARN"]}}]},
        "CacheBehaviors": [{"PathPattern": p, **api_behavior} for p in ["/api", "/api/*", "/auth", "/auth/*", "/studio-config.json"]],
    }}}
    r["WebPolicy"] = {"Type": "AWS::S3::BucketPolicy", "Properties": {"Bucket": {"Ref": "WebBucket"}, "PolicyDocument": {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": {"Service": "cloudfront.amazonaws.com"}, "Action": "s3:GetObject", "Resource": {"Fn::Sub": "${WebBucket.Arn}/*"},
         "Condition": {"StringEquals": {"AWS:SourceArn": {"Fn::Sub": "arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${Distribution}"}}}},
        {"Effect": "Deny", "Principal": "*", "Action": "s3:*", "Resource": [{"Fn::GetAtt": ["WebBucket", "Arn"]}, {"Fn::Sub": "${WebBucket.Arn}/*"}], "Condition": {"Bool": {"aws:SecureTransport": "false"}}},
    ]}}}
    return {"AWSTemplateFormatVersion": "2010-09-09", "Description": "Agent Studio private frontend and approved HTTP VPC origin; backend readiness required",
            "Parameters": {
                "PrivateOriginArn": {"Type": "String", "Description": "Healthy new isolated EC2 instance or private ALB ARN, never an old demo resource"},
                "PrivateOriginHostname": {"Type": "String", "AllowedPattern": "[a-zA-Z0-9.-]+", "Description": "Private DNS name of this isolated EC2 origin, never a public backend"},
            }, "Resources": r, "Outputs": {
                "ApplicationOrigin": {"Value": {"Fn::Sub": "https://${Distribution.DomainName}"}},
                "DistributionId": {"Value": {"Ref": "Distribution"}}, "FrontendBucket": {"Value": {"Ref": "WebBucket"}},
            }}

if __name__ == "__main__":
    print(json.dumps(template(), indent=2))
