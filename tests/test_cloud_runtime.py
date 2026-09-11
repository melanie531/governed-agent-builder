import json
from pathlib import Path
import sqlite3
from infra.compute import network_template, runtime_template, ingress_template
from infra.backup import backup


def test_network_no_public_origin_or_workload_admin():
    r = network_template()['Resources']
    assert not r['PrivateSubnet']['Properties']['MapPublicIpOnLaunch']
    sg = r['OriginSecurityGroup']['Properties']
    assert not sg.get('SecurityGroupIngress')
    assert sg['SecurityGroupEgress'][0]['FromPort'] == 443
    v = r['DataVolume']
    assert v['DeletionPolicy'] == v['UpdateReplacePolicy'] == 'Retain'
    assert v['Properties']['Encrypted']
    role = r['Role']['Properties']
    assert not role.get('ManagedPolicyArns')
    statements = role['Policies'][0]['PolicyDocument']['Statement']
    actions = [a for x in statements for a in x['Action']]
    assert not any(a.startswith(('iam:', 'bedrock:', 'cognito-idp:')) for a in actions)
    assert all(a != '*' for a in actions)
    assert r['Artifacts']['DeletionPolicy'] == 'Retain'


def test_instance_persistent_attachment_private_imdsv2():
    r = runtime_template()['Resources']
    p = r['Instance']['Properties']
    assert not p['NetworkInterfaces'][0]['AssociatePublicIpAddress']
    assert 'KeyName' not in p
    assert p['MetadataOptions']['HttpTokens'] == 'required'
    assert p['BlockDeviceMappings'][0]['Ebs']['Encrypted']
    assert r['DataAttachment']['Properties']['VolumeId'] == {'Ref': 'DataVolumeId'}
    script = p['UserData']['Fn::Base64']['Fn::Sub']
    for text in ['--require-hashes', '--no-index', 'RequiresMountsFor=/data', 'mountpoint -q /data', 'flock --nonblock', 'device/serial', 'Restart=on-failure', 'access_log off', 'UMask=0077']:
        assert text in script
    assert 'set -x' not in script


def test_only_service_managed_security_group_origin_ingress():
    p = ingress_template()['Resources']['CloudFrontOnly']['Properties']
    assert p['FromPort'] == p['ToPort'] == 80
    assert p['SourceSecurityGroupId'] == {'Ref': 'CloudFrontManagedSecurityGroup'}
    assert 'CidrIp' not in p and 'CidrIpv6' not in p


def test_backup_includes_wal_and_restores(tmp_path):
    source = tmp_path/'source.sqlite'
    with sqlite3.connect(source) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE probe(value TEXT)')
        db.execute("INSERT INTO probe VALUES ('synthetic-persistent')")
        db.commit()
        result = backup(source, tmp_path/'backups')
        with sqlite3.connect(result) as restored:
            assert restored.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert restored.execute('SELECT value FROM probe').fetchone()[0] == 'synthetic-persistent'
    assert result.stat().st_mode & 0o077 == 0
