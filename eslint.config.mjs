import { createConfig } from "eslint-config-nick2bad4u";
import tseslint from "typescript-eslint";

/** @type {import("eslint").Linter.Config[]} */
const config = [
    ...createConfig({
        allowDefaultProjectFilePatterns: [],
        plugins: {
            // CSS is linted directly by Stylelint so diagnostics are not
            // duplicated through ESLint's Stylelint bridge.
            "stylelint-2": false,
        },
        rootDirectory: import.meta.dirname,
    }),
    {
        ...tseslint.configs.disableTypeChecked,
        files: ["src/growcam/static/app.js"],
        languageOptions: {
            parserOptions: {
                program: null,
                project: false,
                projectService: false,
            },
            sourceType: "script",
        },
        name: "GrowCam: classic browser application",
        rules: {
            ...tseslint.configs.disableTypeChecked.rules,
            "@typescript-eslint/explicit-member-accessibility": "off",
            "@typescript-eslint/no-use-before-define": "off",
            // This deferred page-lifetime script is neither imported nor
            // evaluated on a server, and its listeners die with the document.
            "etc-misc/no-dom-globals-in-module-scope": "off",
            "etc-misc/typescript/no-unsafe-object-assign": "off",
            "import-x/unambiguous": "off",
            // Internal functions and state are ordered by feature flow rather
            // than as a public module API.
            "jsdoc/no-blank-blocks": "off",
            "jsdoc/require-jsdoc": "off",
            // Internal async functions report failures through their callers;
            // public JSDoc is intentionally not required in this classic app.
            "jsdoc/require-throws": "off",
            "listeners/no-inline-function-event-listener": "off",
            "listeners/no-missing-remove-event-listener": "off",
            "n/no-unsupported-features/node-builtins": "off",
            // Media fallbacks are deliberately sequential, and stored
            // in-flight promises use finally() to clear deduplication state.
            "no-await-in-loop": "off",
            "perfectionist/sort-modules": "off",
            "promise/prefer-await-to-then": "off",
            // Early returns are clearer than synthetic final else branches.
            "sonarjs/elseif-without-else": "off",
            // Repeated short UI labels are clearer at their render sites.
            "sonarjs/no-duplicate-string": "off",
            "unicorn/no-top-level-assignment-in-function": "off",
            "unicorn/prefer-await": "off",
            // Error.isError is not in the unbundled app's browser floor and
            // conflicts with the preset's extended-native prohibition.
            "unicorn/prefer-error-is-error": "off",
            // Explicit window access documents browser-only persistence and is
            // part of the server-tested static app contract.
            "unicorn/prefer-global-this": "off",
            // Guard order is intentional around optional browser/API state.
            "unicorn/prefer-simple-condition-first": "off",
            "unicorn/prefer-switch": "off",
            // Temporal is not yet part of this unbundled app's browser floor.
            "unicorn/prefer-temporal": "off",
            "unicorn/prefer-top-level-await": "off",
            // These orchestration functions centralize cleanup and rollback;
            // splitting their try blocks would obscure those guarantees.
            "unicorn/try-complexity": "off",
        },
    },
    {
        files: ["website/**/*.{js,jsx,mjs,cjs}"],
        name: "GrowCam: Docusaurus module aliases",
        rules: {
            // JavaScript cannot express a return type annotation and the rule
            // does not treat JSDoc returns as an explicit boundary type.
            "@typescript-eslint/explicit-module-boundary-types": "off",
            "import-x/no-unresolved": [
                "error",
                {
                    ignore: ["^@docusaurus/", "^@theme/"],
                },
            ],
        },
    },
    {
        files: ["website/src/pages/**/index.{js,jsx}"],
        name: "GrowCam: Docusaurus page entrypoints",
        rules: {
            "canonical/filename-no-index": "off",
        },
    },
];

export default config;
