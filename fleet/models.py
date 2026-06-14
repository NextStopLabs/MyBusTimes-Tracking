from django.db import models
from main.models import CustomUser, region


class mapTileSet(models.Model):
    name = models.CharField(max_length=100, unique=True)
    tile_url = models.CharField(max_length=255)
    attribution = models.CharField(max_length=255, blank=True, null=True)
    is_default = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Map Tile Sets"


class liverie(models.Model):
    id = models.AutoField(primary_key=True, db_index=True)
    name = models.CharField(max_length=255, db_index=True, blank=True)
    colour = models.CharField(max_length=100)
    left_css = models.TextField(max_length=2048, blank=True, verbose_name="Left CSS")
    right_css = models.TextField(max_length=2048, blank=True, verbose_name="Right CSS")
    text_colour = models.CharField(max_length=100, blank=True)
    stroke_colour = models.CharField(max_length=100, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)
    published = models.BooleanField(default=False)
    declined = models.BooleanField(default=False)
    added_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=False, related_name='livery_added_by')
    aproved_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, related_name='livery_aproved_by', blank=True, null=True)

    class Meta:
        ordering = ("name",)
        verbose_name_plural = "liveries"

    def __str__(self):
        return f"{self.id} - {self.name}"


class vehicleType(models.Model):
    id = models.AutoField(primary_key=True)
    type_name = models.CharField(max_length=100, blank=False)
    active = models.BooleanField(default=False)
    hidden = models.BooleanField(default=False)
    double_decker = models.BooleanField(default=False)
    lengths = models.TextField(blank=True)
    type = models.CharField(blank=False, default='Bus')
    fuel = models.CharField(blank=False, default='Diesel')
    added_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=False, related_name='types_added_by')
    aproved_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, related_name='types_aproved_by')

    def __str__(self):
        return self.type_name


class group(models.Model):
    id = models.AutoField(primary_key=True)
    group_name = models.CharField(blank=False, unique=True)
    group_owner = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=False, related_name='group_owner')

    class OrderBy(models.TextChoices):
        FLEET_NUMBER = 'fleet_number', 'Fleet Number'
        OPERATOR_NAME = 'operator_name', 'Operator Name'

    order_by = models.CharField(
        max_length=20,
        choices=OrderBy.choices,
        default=OrderBy.FLEET_NUMBER,
    )
    private = models.BooleanField(default=False)

    def __str__(self):
        return self.group_name


class organisation(models.Model):
    id = models.AutoField(primary_key=True)
    organisation_name = models.CharField(blank=False)
    organisation_owner = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=False, related_name='organisation_owner')

    def __str__(self):
        return self.organisation_name


def default_operator_details():
    return {
        "website": "https://example.com",
        "twitter": "@example",
        "game": "OMSI2",
        "type": "real-company",
        "transit_authorities": "TFL, TfGM",
    }


class MBTOperator(models.Model):
    id = models.AutoField(primary_key=True)
    operator_name = models.CharField(blank=False, unique=True)
    operator_code = models.CharField(blank=False, unique=True)
    operator_slug = models.SlugField(unique=True, blank=True, max_length=255)
    operator_details = models.JSONField(default=default_operator_details, blank=True, null=True)
    private = models.BooleanField(default=False)
    public = models.BooleanField(default=False)
    show_trip_id = models.BooleanField(default=True)
    vehicles_for_sale = models.IntegerField(default=0)
    owner = models.ForeignKey(CustomUser, on_delete=models.CASCADE, blank=False, related_name='owner')
    group = models.ForeignKey(group, on_delete=models.SET_NULL, blank=True, null=True)
    organisation = models.ForeignKey(organisation, on_delete=models.SET_NULL, blank=True, null=True)
    region = models.ManyToManyField(region, related_name='operators')
    mapTile = models.ForeignKey(mapTileSet, on_delete=models.SET_NULL, null=True, blank=True, related_name='operators')
    verified = models.BooleanField(default=False)
    public_notes = models.TextField(blank=True)

    def __str__(self):
        return self.operator_name


class fleet(models.Model):
    id = models.AutoField(primary_key=True, db_index=True)
    operator = models.ForeignKey(MBTOperator, on_delete=models.SET_NULL, blank=True, null=True, related_name='fleet_operator', db_index=True)
    fleet_number = models.CharField(blank=True, null=True, db_index=True)
    reg = models.CharField(blank=True, null=True, db_index=True)
    livery = models.ForeignKey(liverie, on_delete=models.SET_NULL, null=True, blank=True, db_index=True)
    colour = models.CharField(blank=True, db_index=True)
    vehicleType = models.ForeignKey(vehicleType, on_delete=models.SET_NULL, null=True, db_index=True)
    in_service = models.BooleanField(default=True, db_index=True)

    sim_lat = models.FloatField(blank=True, null=True)
    sim_lon = models.FloatField(blank=True, null=True)
    sim_heading = models.FloatField(blank=True, null=True)
    current_trip = models.ForeignKey('tracking.Trip', on_delete=models.SET_NULL, blank=True, null=True, related_name='fleet_current_trip')
    updated_at = models.DateTimeField(db_index=True, blank=True, null=True)

    def __str__(self):
        parts = []
        if self.fleet_number:
            parts.append(self.fleet_number)
        if self.reg:
            parts.append(self.reg)
        return " - ".join(parts) if parts else f"Vehicle {self.id}"
