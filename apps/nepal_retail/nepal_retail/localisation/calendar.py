from dataclasses import dataclass
from datetime import date, timedelta

BS_EPOCH_AD = date(2013, 4, 14)
BS_MONTH_DAYS: dict[int, tuple[int, ...]] = {
	2070: (31, 31, 31, 32, 31, 31, 29, 30, 30, 29, 30, 30),
	2071: (31, 31, 32, 31, 31, 31, 30, 29, 30, 29, 30, 30),
	2072: (31, 32, 31, 32, 31, 30, 30, 29, 30, 29, 30, 30),
	2073: (31, 32, 31, 32, 31, 30, 30, 30, 29, 29, 30, 31),
	2074: (31, 31, 31, 32, 31, 31, 30, 29, 30, 29, 30, 30),
	2075: (31, 31, 32, 31, 31, 31, 30, 29, 30, 29, 30, 30),
	2076: (31, 32, 31, 32, 31, 30, 30, 30, 29, 29, 30, 30),
	2077: (31, 32, 31, 32, 31, 30, 30, 30, 29, 30, 29, 31),
	2078: (31, 31, 31, 32, 31, 31, 30, 29, 30, 29, 30, 30),
	2079: (31, 31, 32, 31, 31, 31, 30, 29, 30, 29, 30, 30),
	2080: (31, 32, 31, 32, 31, 30, 30, 30, 29, 29, 30, 30),
	2081: (31, 32, 31, 32, 31, 30, 30, 30, 29, 30, 29, 31),
	2082: (31, 31, 32, 31, 31, 31, 30, 29, 30, 29, 30, 30),
	2083: (31, 31, 32, 31, 31, 31, 30, 29, 30, 29, 30, 30),
	2084: (31, 31, 32, 31, 31, 30, 30, 30, 29, 30, 30, 30),
	2085: (31, 32, 31, 32, 30, 31, 30, 30, 29, 30, 30, 30),
	2086: (31, 31, 32, 31, 31, 31, 30, 29, 30, 29, 30, 30),
	2087: (31, 31, 32, 31, 31, 31, 30, 29, 30, 29, 30, 30),
	2088: (31, 32, 31, 32, 31, 30, 30, 30, 29, 29, 30, 30),
	2089: (31, 32, 31, 32, 31, 30, 30, 30, 29, 30, 29, 31),
	2090: (31, 31, 31, 32, 31, 31, 30, 29, 30, 29, 30, 30),
}


@dataclass(frozen=True, order=True)
class BikramSambatDate:
	year: int
	month: int
	day: int

	def __post_init__(self):
		if any(isinstance(v, bool) or not isinstance(v, int) for v in (self.year, self.month, self.day)):
			raise ValueError("Bikram Sambat year, month and day must be integers")
		if self.year not in BS_MONTH_DAYS:
			raise ValueError(f"Unsupported Bikram Sambat year: {self.year}")
		if not 1 <= self.month <= 12:
			raise ValueError("Bikram Sambat month must be between 1 and 12")
		max_day = BS_MONTH_DAYS[self.year][self.month - 1]
		if not 1 <= self.day <= max_day:
			raise ValueError(f"Bikram Sambat day must be between 1 and {max_day}")

	def isoformat(self) -> str:
		return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"

	def cbms_format(self) -> str:
		return f"{self.year:04d}.{self.month:02d}.{self.day:02d}"


@dataclass(frozen=True)
class FiscalYearWindow:
	start_bs_year: int
	end_bs_year: int
	label: str
	cbms_label: str
	start_bs: BikramSambatDate
	end_bs: BikramSambatDate
	start_ad: date
	end_ad: date


def bs_to_ad(bs_date: BikramSambatDate) -> date:
	if not isinstance(bs_date, BikramSambatDate):
		raise ValueError("Expected a BikramSambatDate instance")
	offset = 0
	for year in range(min(BS_MONTH_DAYS), bs_date.year):
		offset += sum(BS_MONTH_DAYS[year])
	offset += sum(BS_MONTH_DAYS[bs_date.year][: bs_date.month - 1])
	offset += bs_date.day - 1
	return BS_EPOCH_AD + timedelta(days=offset)


def ad_to_bs(ad_date: date) -> BikramSambatDate:
	if not isinstance(ad_date, date) or hasattr(ad_date, "hour"):
		raise ValueError("Expected a calendar date instance")
	remaining = (ad_date - BS_EPOCH_AD).days
	if remaining < 0:
		raise ValueError("Date precedes the supported Bikram Sambat calendar table")
	for year in sorted(BS_MONTH_DAYS):
		year_days = sum(BS_MONTH_DAYS[year])
		if remaining < year_days:
			for month_index, month_days in enumerate(BS_MONTH_DAYS[year], start=1):
				if remaining < month_days:
					return BikramSambatDate(year, month_index, remaining + 1)
				remaining -= month_days
		remaining -= year_days
	raise ValueError("Date exceeds the supported Bikram Sambat calendar table")


def fiscal_year_window(start_bs_year: int) -> FiscalYearWindow:
	if isinstance(start_bs_year, bool) or not isinstance(start_bs_year, int):
		raise ValueError("Fiscal year start must be an integer Bikram Sambat year")
	end_bs_year = start_bs_year + 1
	if start_bs_year not in BS_MONTH_DAYS or end_bs_year not in BS_MONTH_DAYS:
		raise ValueError("Fiscal year falls outside the supported Bikram Sambat calendar range")
	start_bs = BikramSambatDate(start_bs_year, 4, 1)
	ashadh_days = BS_MONTH_DAYS[end_bs_year][2]
	end_bs = BikramSambatDate(end_bs_year, 3, ashadh_days)
	short_end = f"{end_bs_year % 100:02d}"
	return FiscalYearWindow(
		start_bs_year=start_bs_year,
		end_bs_year=end_bs_year,
		label=f"{start_bs_year}/{short_end}",
		cbms_label=f"{start_bs_year}.{short_end}",
		start_bs=start_bs,
		end_bs=end_bs,
		start_ad=bs_to_ad(start_bs),
		end_ad=bs_to_ad(end_bs),
	)


def fiscal_year_for_date(ad_date: date) -> FiscalYearWindow:
	bs_date = ad_to_bs(ad_date)
	start_year = bs_date.year if bs_date.month >= 4 else bs_date.year - 1
	return fiscal_year_window(start_year)
