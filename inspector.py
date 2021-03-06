import boto3
import re
import csv
import ast
import json
import requests
from constants import RULE_MAP


class InspectorEngine:
    def __init__(self):
        """
        Create a low-level client representing Amazon Inspector, EC2 instance.
        """
        self.inspector = boto3.client('inspector')
        self.ec2 = boto3.client('ec2')

    def create_awsscan_tag(self, instanceids, tagvalue, tagkey="awsscan"):
        """
        Add a tag to the EC2 instances.
        instanceids: [list] instnace ids.
        tagvalue: [str] tag value name.
        tagkey: [str] tag key name(default is awsscan).
        """
        self.ec2.create_tags(Resources=instanceids, Tags=[{"Key": tagkey, "Value": tagvalue}])

    def create_assessment_target(self, targetname, tagname, tagkey="awsscan"):
        """
        Create an assessment target in Inspector.
        targetname: [str] user-defined assessment target name.
        tagname: [str] tag value name.
        tagkey: [str] tag key name(default is awsscan).
        return [str] assessmentTargetArn.
        """
        resourcegroup_tags = [{"key": tagkey, "value": tagname}]
        resourcegroup_arn = self.inspector.create_resource_group(
            resourceGroupTags=resourcegroup_tags).get("resourceGroupArn")
        response = self.inspector.create_assessment_target(
            assessmentTargetName=targetname, resourceGroupArn=resourcegroup_arn)
        return response.get("assessmentTargetArn")

    def get_rulepackagearns(self, region):
        """
        Generate rulepackages from RULE MAP.
        region: [str] region name to find rule package arns. e.g. "us-east-1", "us-west-2".
        return [list] rulepackagearns.
        """
        rulepackagearns = [value for rule, value in RULE_MAP.get(region).items()]
        return rulepackagearns

    def create_assessment_template(self, targetarn, templatename, rulepackagearns, duration=3600):
        """
        Create an assessment template with rulepackagearns and targentarns.
        targetarn: [str] arn that specifies the assessment target.
        templatename: [str]user-defined template name.
        rulepackagearns: [list] Arns that specify the rules packages that you want to attach to the template.
        duration: [int] the duration of the assessment run in seconds. (default is 1 hour).
        return [str] assessmentTemplateArn.
        """
        response = self.inspector.create_assessment_template(assessmentTargetArn=targetarn,
                                                             assessmentTemplateName=templatename,
                                                             durationInSeconds=duration,
                                                             rulesPackageArns=rulepackagearns)
        return response.get("assessmentTemplateArn")

    def start_assessment_run(self, templatearn):
        """
        Start assessment with tempaltearn.
        templatearn: [str] Arn of the assessment template.
        return [str] assessmentRunArn.
        """
        response = self.inspector.start_assessment_run(assessmentTemplateArn=templatearn)
        return response.get("assessmentRunArn")

    def subscribe_to_event(self, templatearn, topicarn):
        """
        Register events in templatearn.
        templatearn: [str] Arn of the assessment template.
        topicarn: [str] SNS topic to which the SNS notification is sent.
        """
        events = ['ASSESSMENT_RUN_STARTED', 'ASSESSMENT_RUN_COMPLETED',
                  'ASSESSMENT_RUN_STATE_CHANGED']  # ,'FINDING_REPORTED']
        for event in events:
            response = self.inspector.subscribe_to_event(resourceArn=templatearn, event=event, topicArn=topicarn)

    def pull_list_finding(self, agentids, severities, runarns):
        """
        Get findings that are generated by the assessment runs and add CVE correlated feeds.
        agentids: [list]instance ids
        severities: [list] 'Low'|'Medium'|'High'|'Informational'|'Undefined'
        runarns: [list] ARNs of the assessment runs that generate the findings that you want to list.
        return [dict] data.
        """
        report_keys = ["title", "description", "severity", "recommendation"]
        # option
        filter_input = {}
        filter_input["agentIds"] = agentids
        filter_input["severities"] = severities
        nexttoken = None
        cvepattern = re.compile('^(CVE-(1999|2\d{3})-(0\d{2}[1-9]|[1-9]\d{3,}))$')
        data = []
        while True:
            if nexttoken:
                findings = self.inspector.list_findings(assessmentRunArns=runarns, filter=filter_input, nextToken=nexttoken)
            else:
                findings = self.inspector.list_findings(assessmentRunArns=runarns, filter=filter_input)
            nexttoken = findings.get('nextToken')
            findingArn = findings.get("findingArns")
            # get finding and extract detail
            response = self.inspector.describe_findings(findingArns=findingArn, locale='EN_US')
            findings = response.get('findings')
            for finding in findings:
                finding_id = finding.get('id')
                report = {}
                for key in report_keys:
                    report[key] = finding[key]
                if re.match(cvepattern, finding_id):
                    feed = {"id": finding_id, 'report': report, 'feeds': self.get_feeds(finding_id)}
                    data.append(feed)
                else:
                    data.append({"id": finding_id, 'report': report})
            if not nexttoken:
                break
        return data

    def genearte_report(self, agentids, severities, runarns, reportfile):
        """
        Generate JSON format report file.
        agentids: [list] instance ids.
        severities: [list] 'Low'|'Medium'|'High'|'Informational'|'Undefined'
        runarns: [list] ARNs of the assessment runs that generate the findings that you want to list.
        reportfile: [str] report file name/path.
        """
        data = self.pull_list_finding(agentids, severities, runarns)
        rf = open(reportfile, "w")
        json.dump(data, rf, indent=4)
        rf.close()

    def get_feeds(self, cveid):
        """
        Get feed information from http://cve.circl.lu/api/cve/
        cveid: CVE ID.
        """
        SEARCHURL = "http://cve.circl.lu/api/cve/" + cveid
        r = requests.get(SEARCHURL)
        data = None
        if r.status_code == 200:
            data = json.loads(r.text)
        return data
