<!-- One page, often read on a phone: no sidebar, breadcrumbs or SQL toggles. -->
<script>
	import '@evidence-dev/tailwind/fonts.css';
	import '../app.css';
	import { EvidenceDefaultLayout } from '@evidence-dev/core-components';
	export let data;
</script>

<EvidenceDefaultLayout
	{data}
	title="MLB staging data"
	hideSidebar={true}
	hideBreadcrumbs={true}
	neverShowQueries={true}
	builtWithEvidence={false}
	githubRepo="https://github.com/fireplacetv/mlb-data-pipeline"
>
	<slot slot="content" />
</EvidenceDefaultLayout>
