from app.parser import TrafficSample


class AnalyticsService:
    """Computes usage metrics from normalized traffic samples."""

    def usage_percent(self, sample: TrafficSample) -> float | None:
        if not sample.total_bytes:
            return None
        return min(sample.used_bytes / sample.total_bytes * 100, 100.0)
