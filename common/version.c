#include "config.h"
#include <ccan/str/str.h>
#include <common/configvar.h>
#include <common/version.h>
#include <stdio.h>

/* Only common/version.c can safely include this.  */
# include "version_gen.h"

const char *version(void)
{
	return VERSION;
}

static char *version_and_exit(const void *unused UNUSED)
{
	printf("%s\n", VERSION);
	if (BUILD_FEATURES[0]) {
		printf("Built with features: %s\n", BUILD_FEATURES);
	}
	exit(0);
}

void opt_register_version(void)
{
	clnopt_noarg("--version|-V", OPT_EARLY|OPT_EXITS,
		     version_and_exit, NULL,
		     "Print version and exit");
}

static bool cmp_release_version(const char *version) {
	size_t n, suffix;
	if (version[0] != 'v')
		return false;
	n = strspn(version+1, ".0123456789");
	/* Our releases append -blake2b.N to the upstream version */
	if (strstarts(version+1+n, "-blake2b.")) {
		n += strlen("-blake2b.");
		suffix = strspn(version+1+n, "0123456789");
		if (suffix == 0)
			return false;
		n += suffix;
	}
	return n == strlen(version+1);
}

/* Released versions are of form v[year].[month]?(.patch)*(-blake2b.N)? */
bool is_released_version(void)
{
	return cmp_release_version(version());
}
