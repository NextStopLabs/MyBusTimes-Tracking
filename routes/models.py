from django.db import models
from fleet.models import MBTOperator


class route(models.Model):
    id = models.AutoField(primary_key=True)
    hidden = models.BooleanField(default=False)
    route_num = models.CharField(max_length=255, blank=True, null=True)
    route_name = models.CharField(max_length=255, blank=True, null=True)
    inbound_destination = models.CharField(max_length=255, blank=True, null=True)
    outbound_destination = models.CharField(max_length=255, blank=True, null=True)
    other_destination = models.JSONField(blank=True, null=True)
    route_operators = models.ManyToManyField(MBTOperator, blank=False, related_name='route_other_operators')

    def __str__(self):
        parts = [self.route_num]
        if self.route_name:
            parts.append(self.route_name)
        if self.inbound_destination:
            parts.append(self.inbound_destination)
        if self.outbound_destination:
            parts.append(self.outbound_destination)
        return " - ".join(filter(None, parts))


class routeStop(models.Model):
    route = models.ForeignKey(route, on_delete=models.CASCADE)
    inbound = models.BooleanField(default=True)
    circular = models.BooleanField(default=False)
    stops = models.JSONField()
    snapped_route = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.route.id}"


class duty(models.Model):
    duty_name = models.CharField(max_length=100)
    duty_operator = models.ForeignKey(MBTOperator, on_delete=models.CASCADE, related_name='duties', blank=True, null=True)
    board_type = models.CharField(max_length=20, choices=[
        ('duty', 'Duty'),
        ('running-boards', 'Running Board'),
    ], default='duty')

    def __str__(self):
        board_type = "Running Board" if self.board_type == "running-boards" else "Duty"
        return f"{self.duty_name if self.duty_name else 'Unnamed Duty'} ({board_type})"
