import sharedConfig from "stylelint-config-nick2bad4u";

/** @type {import("stylelint").Config} */
const stylelintConfig = {
    ...sharedConfig,
    languageOptions: {
        ...sharedConfig.languageOptions,
        directionality: {
            block: "top-to-bottom",
            inline: "left-to-right",
        },
    },
    overrides: [
        ...(sharedConfig.overrides ?? []),
        {
            files: ["src/growcam/static/**/*.css"],
            rules: {
                // The Python dashboard is not a Docusaurus surface.
                "docusaurus/no-color-scheme-on-docusaurus-html-root": null,
                "docusaurus/no-hardcoded-docusaurus-breakpoint-values": null,
                "docusaurus/no-unscoped-content-element-overrides": null,
            },
        },
    ],
};

export default stylelintConfig;
